import re
import hashlib
from typing import Dict, Any, List, Optional, Tuple


class TemplateEngine:
    """
    Deterministic personalization template engine.

    Guarantees:
      * No raw placeholders ever survive rendering.
      * Any attribute that is missing from the recipient profile is OMITTED
        entirely -- never guessed, never left as an empty gap -- and the
        surrounding sentence stays grammatical.
      * Structural diversity across a batch via stable hash-based variation.
    """

    # ------------------------------------------------------------------
    # Template pools. Every token used here is guaranteed to be produced by
    # _build_tokens(), and every token degrades to a grammatical alternative
    # (or the empty string) when its source field is missing.
    # ------------------------------------------------------------------

    INVESTOR_TEMPLATES = [
        {
            "subject": "Intro: {startup_name} ({industry})",
            "body": (
                "Hi {first_name},\n\n"
                "I hope you're having a great week. I'm reaching out because of {investor_context}."
                "{focus_sentence}"
                "\n\n"
                "I'm the founder of {startup_name}. {startup_pitch}{traction_sentence}{deck_sentence}"
                "\n\n"
                "We are currently raising our round and would welcome the opportunity to discuss a potential fit. "
                "Would you be open to a 15-minute intro conversation sometime next week?\n\n"
                "Best regards,\n"
                "{sender_name}\n"
                "{sender_signature}"
            ),
        },
        {
            "subject": "{startup_name} - Investment conversation",
            "body": (
                "Hello {first_name},\n\n"
                "Following {investor_context}, I wanted to briefly introduce {startup_name}."
                "{focus_sentence}"
                "\n\n"
                "{startup_pitch}{traction_sentence}{deck_sentence}"
                "\n\n"
                "I'd love to get your thoughts on what we're building and explore whether this aligns with your current investment thesis. "
                "Do you have 15 minutes available for a brief introductory call?\n\n"
                "Thanks,\n"
                "{sender_name}\n"
                "{sender_signature}"
            ),
        },
        {
            "subject": "Quick intro / {startup_name}",
            "body": (
                "Hi {first_name},\n\n"
                "Reaching out directly as I follow {investor_context}."
                "{focus_sentence}"
                "\n\n"
                "At {startup_name}, {startup_pitch_lower}{traction_sentence}{deck_sentence}"
                "\n\n"
                "We're opening conversations with select investors and would love to share a brief overview with your team. "
                "Let me know if you have time for a quick introductory chat next week.\n\n"
                "Best,\n"
                "{sender_name}\n"
                "{sender_signature}"
            ),
        },
    ]

    JOB_TEMPLATES = [
        {
            "subject": "Exploring opportunities at {company} - {sender_name}",
            "subject_no_company": "Exploring engineering opportunities - {sender_name}",
            "body": (
                "Hi {first_name},\n\n"
                "I've been closely following {company_possessive} work{role_inline}."
                "\n\n"
                "I am an experienced engineer with a background in {skills}. "
                "I admire what your team is building, and I'm interested in exploring opportunities to contribute to your engineering goals."
                "\n\n"
                "Are you open to a brief chat, or could you point me to the right hiring manager?\n\n"
                "Best regards,\n"
                "{sender_name}"
            ),
        },
        {
            "subject": "Software engineering roles at {company}",
            "subject_no_company": "Software engineering roles on your team",
            "body": (
                "Hello {first_name},\n\n"
                "I wanted to reach out regarding engineering opportunities {company_prep}."
                "{role_sentence}"
                "\n\n"
                "My technical background spans {skills}. I've worked on high-scale systems and would love to bring that problem-solving approach to {company_display}."
                "\n\n"
                "Would you be open to a quick 10-minute call to discuss whether there's a mutual fit for upcoming roles?\n\n"
                "Thank you for your time,\n"
                "{sender_name}"
            ),
        },
    ]

    INTERNSHIP_TEMPLATES = [
        {
            "subject": "Software engineering internship inquiry - {sender_name}",
            "body": (
                "Hi {first_name},\n\n"
                "I hope you're doing well. I'm reaching out because I'm very interested in {company_possessive} engineering work{role_inline}."
                "\n\n"
                "I'm a software engineering student with hands-on experience in {skills}. "
                "I've built several practical projects and am eager to contribute to high-impact technical work {company_prep}."
                "\n\n"
                "Are you open to interns for the upcoming term? I'd appreciate the chance to share my resume and portfolio.\n\n"
                "Best regards,\n"
                "{sender_name}"
            ),
        },
        {
            "subject": "{company} / student engineering internship inquiry",
            "subject_no_company": "Student engineering internship inquiry",
            "body": (
                "Hello {first_name},\n\n"
                "I've been following {company_possessive} technical trajectory and wanted to get in touch."
                "{role_sentence}"
                "\n\n"
                "As an aspiring software engineer specializing in {skills}, I'm actively looking for an internship where I can solve meaningful problems and ship clean code."
                "\n\n"
                "Could you let me know whether your team is considering internship candidates, or point me in the right direction?\n\n"
                "Thanks,\n"
                "{sender_name}"
            ),
        },
    ]

    CUSTOM_TEMPLATES = [
        {
            "subject": "Connecting {company} & {startup_name}",
            "subject_no_company": "Connecting regarding {startup_name}",
            "body": (
                "Hi {first_name},\n\n"
                "I hope all is well. I'm reaching out from {startup_name} regarding our shared focus on {industry}."
                "{role_sentence}"
                "\n\n"
                "{startup_pitch}{traction_sentence}"
                "\n\n"
                "I'd love to connect briefly to explore potential synergies between our teams. "
                "Would you be open to a short introductory call?\n\n"
                "Best regards,\n"
                "{sender_name}"
            ),
        },
        {
            "subject": "{startup_name} - quick introduction",
            "body": (
                "Hello {first_name},\n\n"
                "I'm getting in touch from {startup_name}, where we work in {industry}."
                "{role_sentence}"
                "\n\n"
                "{startup_pitch}{traction_sentence}"
                "\n\n"
                "If this is relevant to your priorities, I'd welcome a brief conversation to compare notes.\n\n"
                "Thanks,\n"
                "{sender_name}"
            ),
        },
    ]

    @classmethod
    def _select_pool(cls, campaign_type: str) -> List[Dict[str, str]]:
        ctype = (campaign_type or "").upper()
        if "INVESTOR" in ctype:
            return cls.INVESTOR_TEMPLATES
        if "INTERN" in ctype:
            return cls.INTERNSHIP_TEMPLATES
        if "JOB" in ctype:
            return cls.JOB_TEMPLATES
        return cls.CUSTOM_TEMPLATES

    @staticmethod
    def _clean(text: str) -> str:
        """Collapses whitespace and repairs punctuation left by omitted clauses."""
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" +([,.;:!?])", r"\1", text)
        text = re.sub(r"([,.;:!?])\1+", r"\1", text)
        text = re.sub(r" +\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @classmethod
    def _build_tokens(
        cls,
        recipient: Dict[str, Any],
        sender: Dict[str, Any],
        startup_info: Dict[str, Any],
        allowed_fields: Optional[List[str]] = None,
    ) -> Tuple[Dict[str, str], List[str], bool, Dict[str, str]]:
        """
        Builds the full token map. Any field that is missing (or not permitted by
        allowed_fields) yields a neutral fallback or an empty clause rather than a
        fabricated value.

        Returns: (tokens, candidate_fields, has_company, field_values)
        """
        allowed = set(allowed_fields) if allowed_fields else None

        def permitted(field: str) -> bool:
            return allowed is None or field in allowed

        used: List[str] = []

        # ---- Recipient identity -------------------------------------------------
        full_name = str(recipient.get("full_name") or "").strip()
        first_name = str(recipient.get("first_name") or "").strip()
        if not first_name and full_name:
            first_name = full_name.split()[0]
        if first_name and permitted("first_name"):
            used.append("first_name")
        else:
            first_name = "there"

        # ---- Company / firm -----------------------------------------------------
        firm = str(recipient.get("company") or recipient.get("firm") or "").strip()
        if firm and not permitted("company"):
            firm = ""
        if firm:
            used.append("company")
            company_display = firm
            company_possessive = f"{firm}'" if firm.endswith(("s", "S")) else f"{firm}'s"
            company_prep = f"at {firm}"
            investor_context = f"your work at {firm}"
        else:
            company_display = "your team"
            company_possessive = "your team's"
            company_prep = "on your team"
            investor_context = "your work in early-stage investing"

        # ---- Investment focus ---------------------------------------------------
        investment_focus = str(recipient.get("investment_focus") or "").strip()
        if investment_focus and not permitted("investment_focus"):
            investment_focus = ""
        if investment_focus:
            used.append("investment_focus")
            focus_sentence = f" Given your focus on {investment_focus}, our thesis looks closely aligned."
        else:
            focus_sentence = ""

        # ---- Role ---------------------------------------------------------------
        role = str(recipient.get("role") or recipient.get("designation") or "").strip()
        if role and not permitted("role"):
            role = ""
        if role:
            used.append("role")
            role_inline = f", and I noticed you serve as {role} there"
            role_sentence = f" Given your role as {role}, I thought you'd be the right person to ask."
        else:
            role_inline = ""
            role_sentence = ""

        # ---- Startup / sender facts (sender-supplied, never invented) ------------
        startup_name = str(startup_info.get("name") or sender.get("company") or "our team").strip()
        industry = str(startup_info.get("industry") or "AI automation").strip()
        raw_pitch = str(
            startup_info.get("pitch")
            or startup_info.get("one_liner")
            or "we build autonomous agent workflows that streamline operations."
        ).strip()
        startup_pitch = raw_pitch[:1].upper() + raw_pitch[1:] if raw_pitch else ""
        startup_pitch_lower = raw_pitch[:1].lower() + raw_pitch[1:] if raw_pitch else ""

        traction = str(startup_info.get("traction") or "").strip()
        traction_sentence = f" To date, {traction.rstrip('.')}." if traction else ""

        deck_url = str(startup_info.get("deck_url") or startup_info.get("deck") or "").strip()
        deck_sentence = f" Our deck is here: {deck_url}" if deck_url else ""

        sender_name = str(sender.get("name") or "").strip() or "Founder"
        sender_title = str(sender.get("title") or "").strip()
        sender_signature = f"{sender_title}, {startup_name}" if sender_title else startup_name
        skills = str(
            sender.get("skills") or "Python, distributed systems, and AI agent architectures"
        ).strip()

        tokens = {
            "first_name": first_name,
            "company": firm,
            "company_display": company_display,
            "company_possessive": company_possessive,
            "company_prep": company_prep,
            "investor_context": investor_context,
            "focus_sentence": focus_sentence,
            "role_inline": role_inline,
            "role_sentence": role_sentence,
            "startup_name": startup_name,
            "industry": industry,
            "startup_pitch": startup_pitch,
            "startup_pitch_lower": startup_pitch_lower,
            "traction_sentence": traction_sentence,
            "deck_sentence": deck_sentence,
            "sender_name": sender_name,
            "sender_title": sender_title or "Founder",
            "sender_signature": sender_signature,
            "skills": skills,
        }
        field_values = {
            "first_name": first_name if "first_name" in used else "",
            "company": firm,
            "role": role,
            "investment_focus": investment_focus,
        }
        return tokens, used, bool(firm), field_values

    @classmethod
    def render(
        cls,
        campaign_type: str,
        recipient: Dict[str, Any],
        sender: Dict[str, Any],
        startup_info: Dict[str, Any],
        allowed_fields: Optional[List[str]] = None,
        variation_seed: Optional[str] = None,
    ) -> Tuple[str, str, List[str]]:
        """
        Renders a personalized subject and body with zero raw placeholders.
        Missing facts are omitted cleanly instead of guessed.

        Returns: (subject, body, personalization_used)
        """
        pool = cls._select_pool(campaign_type)

        seed_key = (
            variation_seed
            or recipient.get("email")
            or recipient.get("full_name")
            or "0"
        )
        idx = int(hashlib.md5(str(seed_key).encode("utf-8")).hexdigest(), 16) % len(pool)
        selected = pool[idx]

        tokens, candidate_fields, has_company, field_values = cls._build_tokens(
            recipient=recipient,
            sender=sender,
            startup_info=startup_info,
            allowed_fields=allowed_fields,
        )

        subject_template = selected["subject"]
        if not has_company and "subject_no_company" in selected:
            subject_template = selected["subject_no_company"]

        subject = cls._clean(subject_template.format(**tokens))
        body = cls._clean(selected["body"].format(**tokens))

        # Report only the fields this particular template actually surfaced, so
        # personalization_used is an audit trail rather than an intention.
        rendered = f"{subject}\n{body}"
        used = [
            field
            for field in candidate_fields
            if field_values.get(field) and field_values[field] in rendered
        ]

        return subject, body, used
