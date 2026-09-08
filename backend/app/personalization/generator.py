import json
import re
import logging
from typing import Dict, Any, List, Optional

from app.personalization.validator import (
    PersonalizedMessage,
    PersonalizationValidator,
    ValidationStatus,
)
from app.personalization.prompts import (
    PERSONALIZATION_SYSTEM_PROMPT,
    format_batch_personalization_prompt,
)
from app.personalization.templates import TemplateEngine
from app.llm.client import invoke_llm

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_FIELDS = ["first_name", "company", "role", "investment_focus"]


class PersonalizationGenerator:
    """
    AI Personalization Engine generating unique, factual, strictly validated emails.

    Batches recipients into a single LLM call to cut API roundtrips, aligns the
    model's output back to the input recipients by email (never by position),
    and repairs or replaces any message that fails deterministic validation with
    a guaranteed-clean TemplateEngine rendering.
    """

    def __init__(self, batch_size: int = 5, repair_invalid: bool = True):
        self.batch_size = batch_size
        self.repair_invalid = repair_invalid

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_single(
        self,
        campaign_objective: str,
        campaign_type: str,
        recipient: Dict[str, Any],
        sender_profile: Dict[str, Any],
        startup_info: Dict[str, Any],
        campaign_instructions: str = "",
        allowed_fields: Optional[List[str]] = None,
        tone: str = "professional",
        max_length: int = 1500,
        allowed_personalization_fields: Optional[List[str]] = None,
    ) -> PersonalizedMessage:
        """Generates a single personalized message."""
        results = self.generate_batch(
            campaign_objective=campaign_objective,
            campaign_type=campaign_type,
            recipients=[recipient],
            sender_profile=sender_profile,
            startup_info=startup_info,
            campaign_instructions=campaign_instructions,
            allowed_fields=allowed_fields,
            tone=tone,
            max_length=max_length,
            allowed_personalization_fields=allowed_personalization_fields,
        )
        return results[0]

    def generate_batch(
        self,
        campaign_objective: str,
        campaign_type: str,
        recipients: List[Dict[str, Any]],
        sender_profile: Dict[str, Any],
        startup_info: Dict[str, Any],
        campaign_instructions: str = "",
        allowed_fields: Optional[List[str]] = None,
        tone: str = "professional",
        max_length: int = 1500,
        allowed_personalization_fields: Optional[List[str]] = None,
    ) -> List[PersonalizedMessage]:
        """
        Generates personalized messages for a list of recipients, in chunks of
        self.batch_size, then validates the whole batch for correctness,
        duplicates and diversity.
        """
        allowed = allowed_personalization_fields or allowed_fields or DEFAULT_ALLOWED_FIELDS
        all_messages: List[PersonalizedMessage] = []
        validator = PersonalizationValidator()

        for i in range(0, len(recipients), self.batch_size):
            chunk = recipients[i : i + self.batch_size]
            batch_messages = self._process_chunk(
                campaign_objective=campaign_objective,
                campaign_type=campaign_type,
                recipients=chunk,
                sender_profile=sender_profile,
                startup_info=startup_info,
                campaign_instructions=campaign_instructions,
                allowed_fields=allowed,
                tone=tone,
                max_length=max_length,
            )

            for offset, (msg, profile) in enumerate(zip(batch_messages, chunk)):
                validator.validate_message(msg, recipient_profile=profile)

                # A model that hallucinated or left a placeholder must not ship.
                # Replace it with the deterministic rendering, which cannot.
                if self.repair_invalid and msg.validation_status == ValidationStatus.INVALID:
                    repaired = self._render_template_message(
                        campaign_type=campaign_type,
                        recipient=profile,
                        sender_profile=sender_profile,
                        startup_info=startup_info,
                        allowed_fields=allowed,
                        index=i + offset,
                    )
                    original_warnings = list(msg.warnings)
                    validator.validate_message(repaired, recipient_profile=profile)
                    if repaired.validation_status != ValidationStatus.INVALID:
                        repaired.warnings.insert(
                            0,
                            "LLM output failed validation and was replaced by the "
                            f"deterministic template. Original issues: {original_warnings}",
                        )
                        repaired.validation_status = ValidationStatus.WARNING
                        repaired.confidence = min(repaired.confidence, 0.7)
                        msg = repaired

                all_messages.append(msg)

        validator.audit_batch_diversity(all_messages, max_similarity=0.92, n=3)

        return all_messages

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _process_chunk(
        self,
        campaign_objective: str,
        campaign_type: str,
        recipients: List[Dict[str, Any]],
        sender_profile: Dict[str, Any],
        startup_info: Dict[str, Any],
        campaign_instructions: str,
        allowed_fields: List[str],
        tone: str,
        max_length: int,
    ) -> List[PersonalizedMessage]:
        """
        Attempts batch generation via the LLM, falling back per-recipient to the
        deterministic template engine for anything the model did not return.
        """
        prompt = format_batch_personalization_prompt(
            campaign_objective=campaign_objective,
            campaign_type=campaign_type,
            sender_profile=sender_profile,
            startup_info=startup_info,
            recipients=recipients,
            allowed_fields=allowed_fields,
            campaign_instructions=campaign_instructions,
            tone=tone,
            max_length=max_length,
        )

        full_prompt = f"{PERSONALIZATION_SYSTEM_PROMPT}\n\n{prompt}"

        parsed: List[PersonalizedMessage] = []
        try:
            raw_response = invoke_llm(full_prompt)
            parsed = self._parse_llm_json(raw_response) or []
        except Exception as exc:
            logger.warning(
                "LLM batch generation issue (%s). Using deterministic template engine.", exc
            )

        if parsed:
            logger.info("LLM returned %d/%d messages.", len(parsed), len(recipients))

        return self._align_to_recipients(
            parsed=parsed,
            recipients=recipients,
            campaign_type=campaign_type,
            sender_profile=sender_profile,
            startup_info=startup_info,
            allowed_fields=allowed_fields,
        )

    def _align_to_recipients(
        self,
        parsed: List[PersonalizedMessage],
        recipients: List[Dict[str, Any]],
        campaign_type: str,
        sender_profile: Dict[str, Any],
        startup_info: Dict[str, Any],
        allowed_fields: List[str],
    ) -> List[PersonalizedMessage]:
        """
        Maps LLM results onto the input recipients by email, so a reordered,
        partial or over-long model response can never mis-address an email.
        Any recipient the model skipped is rendered from the template engine.
        """
        by_email: Dict[str, PersonalizedMessage] = {}
        for msg in parsed:
            key = (msg.recipient_email or "").strip().lower()
            if key and key not in by_email:
                by_email[key] = msg

        aligned: List[PersonalizedMessage] = []
        for idx, recipient in enumerate(recipients):
            email = str(recipient.get("email") or "").strip()
            match = by_email.pop(email.lower(), None)

            if match is None:
                aligned.append(
                    self._render_template_message(
                        campaign_type=campaign_type,
                        recipient=recipient,
                        sender_profile=sender_profile,
                        startup_info=startup_info,
                        allowed_fields=allowed_fields,
                        index=idx,
                    )
                )
                continue

            # Recipient identity always comes from our own data, never the model's.
            match.recipient_email = email or match.recipient_email
            match.recipient_name = self._display_name(recipient) or match.recipient_name
            aligned.append(match)

        return aligned

    @staticmethod
    def _display_name(recipient: Dict[str, Any]) -> str:
        full_name = str(recipient.get("full_name") or "").strip()
        if full_name:
            return full_name
        parts = [
            str(recipient.get("first_name") or "").strip(),
            str(recipient.get("last_name") or "").strip(),
        ]
        joined = " ".join(p for p in parts if p).strip()
        return joined

    def _render_template_message(
        self,
        campaign_type: str,
        recipient: Dict[str, Any],
        sender_profile: Dict[str, Any],
        startup_info: Dict[str, Any],
        allowed_fields: List[str],
        index: int = 0,
    ) -> PersonalizedMessage:
        """Deterministic, guaranteed-placeholder-free rendering for one recipient."""
        email = str(recipient.get("email") or "").strip()
        name = self._display_name(recipient) or "Valued Contact"

        subject, body, used_fields = TemplateEngine.render(
            campaign_type=campaign_type,
            recipient=recipient,
            sender=sender_profile,
            startup_info=startup_info,
            allowed_fields=allowed_fields,
            variation_seed=f"{email}_{index}",
        )

        return PersonalizedMessage(
            recipient_email=email,
            recipient_name=name,
            subject=subject,
            body=body,
            personalization_used=used_fields,
            confidence=0.95,
            validation_status=ValidationStatus.VALID,
        )

    def _parse_llm_json(self, response_text: str) -> Optional[List[PersonalizedMessage]]:
        """Extracts and parses the JSON array from an LLM response."""
        clean = (response_text or "").strip()
        if not clean:
            return None

        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?", "", clean)
            clean = re.sub(r"```$", "", clean)
            clean = clean.strip()

        start_idx = clean.find("[")
        end_idx = clean.rfind("]")
        if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
            return None

        try:
            data_list = json.loads(clean[start_idx : end_idx + 1])
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Could not parse LLM JSON payload: %s", exc)
            return None

        if not isinstance(data_list, list):
            return None

        messages: List[PersonalizedMessage] = []
        for item in data_list:
            if not isinstance(item, dict):
                continue
            try:
                confidence = float(item.get("confidence", 0.90))
            except (TypeError, ValueError):
                confidence = 0.90

            messages.append(
                PersonalizedMessage(
                    recipient_email=str(item.get("recipient_email") or ""),
                    recipient_name=str(item.get("recipient_name") or ""),
                    subject=str(item.get("subject") or ""),
                    body=str(item.get("body") or ""),
                    personalization_used=list(item.get("personalization_used") or []),
                    confidence=max(0.0, min(1.0, confidence)),
                    validation_status=ValidationStatus.VALID,
                )
            )

        return messages or None
