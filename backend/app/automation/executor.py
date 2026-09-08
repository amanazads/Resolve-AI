import logging
import uuid
from typing import Dict, Any, Callable, Optional, Awaitable
from datetime import datetime, timezone

from app.automation.models import AutomationJob, IdempotencyRecord
from app.automation.job_queue import job_queue
from app.database.mongodb import db_manager

logger = logging.getLogger(__name__)

class DeterministicExecutor:
    """
    Executes automation tasks using strictly registered deterministic tools.
    Guarantees idempotency and crash-resilience.
    Never allows an LLM to directly trigger arbitrary external side-effects.
    """

    def __init__(self):
        self.tools: Dict[str, Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {}
        self._register_default_tools()

    def register_tool(self, action_name: str, handler: Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]):
        """Registers a deterministic tool handler."""
        self.tools[action_name] = handler
        logger.debug(f"Registered tool for action: '{action_name}'")

    def _register_default_tools(self):
        """Registers deterministic mock/dry-run tools (no real bulk email yet)."""

        async def mock_send_email(recipient_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
            subject = payload.get("subject", "AI Automation Notification")
            body = payload.get("body", "This is an automated workflow simulation.")
            logger.info(f"[MOCK_EMAIL] Sent simulated email to '{recipient_id}' (Subject: '{subject}')")
            return {
                "success": True,
                "action": "mock_send_email",
                "recipient_id": recipient_id,
                "subject": subject,
                "status": "Delivered (Dry-Run Simulation)",
                "preview": body[:80] + ("..." if len(body) > 80 else ""),
                "dispatched_at": datetime.now(timezone.utc).isoformat()
            }

        async def mock_phone_call(recipient_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
            logger.info(f"[MOCK_CALL] Simulated phone call to '{recipient_id}'")
            return {
                "success": True,
                "action": "mock_phone_call",
                "recipient_id": recipient_id,
                "call_id": f"MOCK_CALL_{uuid.uuid4().hex[:8]}",
                "status": "Completed (Simulation)",
                "duration_seconds": 30,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

        async def mock_send_sms(recipient_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
            logger.info(f"[MOCK_SMS] Simulated SMS to '{recipient_id}'")
            return {
                "success": True,
                "action": "mock_send_sms",
                "recipient_id": recipient_id,
                "message": payload.get("message", "Automated SMS notification"),
                "status": "Delivered (Simulation)",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

        async def mock_data_fetch(recipient_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
            logger.info(f"[MOCK_DATA] Fetched data for recipient '{recipient_id}'")
            return {
                "success": True,
                "action": "mock_data_fetch",
                "recipient_id": recipient_id,
                "verified": True,
                "attributes": payload.get("attributes", {"tier": "standard", "active": True}),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

        async def mock_order_action(recipient_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
            order_id = payload.get("order_id", "ORD-AUTOMATION-1")
            logger.info(f"[MOCK_ORDER] Processed order action on '{order_id}'")
            return {
                "success": True,
                "action": "mock_order_action",
                "order_id": order_id,
                "recipient_id": recipient_id,
                "status": "Verified & Synced",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

        async def dry_run_action(recipient_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
            action_name = payload.get("action_name", "generic_dry_run")
            logger.info(f"[DRY_RUN] Simulated generic action '{action_name}' for '{recipient_id}'")
            return {
                "success": True,
                "action": action_name,
                "recipient_id": recipient_id,
                "status": "Dry Run Executed Successfully",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

        self.register_tool("mock_send_email", mock_send_email)
        self.register_tool("mock_phone_call", mock_phone_call)
        self.register_tool("mock_send_sms", mock_send_sms)
        self.register_tool("mock_data_fetch", mock_data_fetch)
        self.register_tool("mock_order_action", mock_order_action)
        self.register_tool("dry_run_action", dry_run_action)

    async def execute_job(self, job: AutomationJob) -> Dict[str, Any]:
        """
        Executes an AutomationJob with idempotency protection and atomic state management.
        """
        key = job.idempotency_key
        logger.info(f"Executing job '{job.id}' (Action: '{job.action}', IdempotencyKey: '{key}')")

        # 1. Idempotency Check: never run the same action twice for the same recipient
        existing_record = await db_manager.get_idempotency_record(key)
        if existing_record:
            logger.info(f"Idempotency match found for key '{key}'. Reusing existing result without re-execution.")
            cached_result = existing_record.get("result", {})
            await job_queue.ack(job.id, cached_result)
            return {
                "success": True,
                "idempotent": True,
                "cached": True,
                "result": cached_result
            }

        # 2. Tool Lookup
        tool_handler = self.tools.get(job.action) or self.tools.get("dry_run_action")
        recipient = job.recipient_id or "global"

        # 3. Deterministic Execution
        try:
            result = await tool_handler(recipient, job.payload)
            if not result.get("success", False):
                raise RuntimeError(result.get("error", "Deterministic tool reported failure"))

            # 4. Save Idempotency Record
            idempotency_rec = IdempotencyRecord(
                idempotency_key=key,
                campaign_id=job.campaign_id,
                recipient_id=job.recipient_id,
                action=job.action,
                status="COMPLETED",
                result=result,
                created_at=datetime.now(timezone.utc)
            )
            await db_manager.save_idempotency_record(idempotency_rec.model_dump())

            # 5. Acknowledge Queue
            await job_queue.ack(job.id, result)
            return {
                "success": True,
                "idempotent": False,
                "cached": False,
                "result": result
            }

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error executing job '{job.id}': {error_msg}")
            await job_queue.nack(job.id, error_msg)
            return {
                "success": False,
                "error": error_msg,
                "job_id": job.id
            }

executor = DeterministicExecutor()


# ==========================================================================
# Campaign job executor
#
# The DeterministicExecutor above runs the generic automation plan. The executor
# below runs one campaign job end to end: generate the message, then send it
# through the email provider abstraction -- or, in dry-run mode, stop after
# generation and record exactly what would have been sent.
# ==========================================================================

import asyncio
from typing import Tuple

from app.automation.models import CampaignJob, utc_now
from app.automation.queue import CampaignJobQueue, campaign_job_queue
from app.automation.rate_limiter import RateLimiterRegistry, rate_limiters
from app.automation.retry_service import (
    CampaignRetryPolicy,
    RetryDecision,
    campaign_retry_policy,
)
from app.integrations.base import EmailProvider, SendResult, SendStatus
from app.integrations.registry import get_email_provider
from app.permissions.models import AuditEventType
from app.permissions.service import permission_service
from app.personalization.generator import PersonalizationGenerator
from app.personalization.validator import ValidationStatus


class CampaignJobExecutor:
    """
    Executes a single campaign job.

    The send path is deliberately narrow, because everything that could cause a
    duplicate delivery lives in it:

      1. Consult the idempotency record for this job's key. A completed record
         means this recipient action already went out -- reuse it, never resend.
      2. Move the job to SENDING and bump the attempt count *before* calling the
         provider, so an interruption is visible afterwards.
      3. Call the provider exactly once.
      4. Write the idempotency record and mark the job SENT only when the
         provider confirmed acceptance.
    """

    def __init__(
        self,
        queue: Optional[CampaignJobQueue] = None,
        provider: Optional[EmailProvider] = None,
        retry_policy: Optional[CampaignRetryPolicy] = None,
        limiters: Optional[RateLimiterRegistry] = None,
        generator: Optional[PersonalizationGenerator] = None,
        db=None,
    ):
        self.queue = queue or campaign_job_queue
        self._provider = provider
        self.retry_policy = retry_policy or campaign_retry_policy
        self.limiters = limiters or rate_limiters
        self.generator = generator or PersonalizationGenerator()
        self._db = db or db_manager
        # campaign_id -> owning user, so per-message audit events are attributable
        # without a database read for every recipient.
        self._owner_cache: Dict[str, Optional[str]] = {}

    @property
    def provider(self) -> EmailProvider:
        return self._provider or get_email_provider()

    async def _owner_of(self, campaign_id: str) -> Optional[str]:
        if campaign_id not in self._owner_cache:
            campaign = await self._db.get_campaign(campaign_id) or {}
            self._owner_cache[campaign_id] = campaign.get("owner_id")
        return self._owner_cache[campaign_id]

    async def _audit_message(
        self,
        job: CampaignJob,
        event: AuditEventType,
        result: Optional[SendResult] = None,
        detail: str = "",
    ) -> None:
        """
        Records the outcome of one message.

        Every recipient gets an audit line even though only the campaign was
        authorized once -- that is what makes a single broad approval reviewable
        after the fact.
        """
        await permission_service.audit(
            event,
            user_id=await self._owner_of(job.campaign_id),
            actor="campaign_worker",
            integration=result.provider if result else job.provider,
            campaign_id=job.campaign_id,
            job_id=job.id,
            contact_id=job.contact_id,
            recipient=job.to_email,
            message_id=result.message_id if result else job.message_id,
            outcome=(result.status.value if result else None),
            detail=detail,
            metadata={
                "attempt": job.attempt_count,
                "dry_run": job.dry_run,
                "subject": job.generated_subject,
            },
        )

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    async def generate(
        self,
        job: CampaignJob,
        worker_id: str,
        campaign_context: Dict[str, Any],
    ) -> Optional[CampaignJob]:
        """
        Composes the message for one recipient and stores it on the job.

        Generation is CPU/LLM-bound and synchronous, so it runs in a thread to
        keep the worker's event loop responsive while a batch is in flight.
        """
        if job.generated_body and job.generated_subject:
            # Already generated on an earlier attempt: never pay for it twice.
            return await self.queue.mark_generated(
                job,
                worker_id,
                job.generated_subject,
                job.generated_body,
                job.generation_metadata,
            )

        try:
            message = await asyncio.to_thread(
                self.generator.generate_single,
                campaign_objective=campaign_context.get("objective", ""),
                campaign_type=campaign_context.get("campaign_type", "CUSTOM_OUTREACH"),
                recipient=job.recipient,
                sender_profile=campaign_context.get("sender_profile", {}),
                startup_info=campaign_context.get("startup_info", {}),
                campaign_instructions=campaign_context.get("instructions", ""),
                allowed_fields=campaign_context.get("personalization_fields"),
                tone=campaign_context.get("tone", "professional"),
            )
        except Exception as exc:
            logger.exception("Message generation failed for job '%s'", job.id)
            return await self.queue.mark_failed(
                job,
                worker_id,
                reason=f"Message generation failed: {exc}",
                failure_code="generation_error",
            )

        if message.validation_status == ValidationStatus.INVALID:
            # A message that failed validation must not be sent to a real person.
            return await self.queue.mark_skipped(
                job,
                worker_id,
                reason=f"Generated message failed validation: {'; '.join(message.warnings)}",
                failure_code="validation_failed",
            )

        gen_job = await self.queue.mark_generated(
            job,
            worker_id,
            subject=message.subject,
            body=message.body,
            metadata={
                "validation_status": message.validation_status.value,
                "personalization_used": message.personalization_used,
                "confidence": message.confidence,
                "warnings": message.warnings,
            },
        )
        if gen_job:
            try:
                from app.campaigns.events import campaign_event_bus, CampaignEventType
                await campaign_event_bus.emit(
                    event_type=CampaignEventType.MESSAGE_GENERATED,
                    campaign_id=job.campaign_id,
                    job_id=job.id,
                    worker_id=worker_id,
                    recipient_email=job.to_email,
                    details=f"Personalized message generated for {job.to_email}: '{message.subject}'",
                    metadata={"confidence": message.confidence},
                )
            except Exception as e:
                logger.debug("Failed to emit MESSAGE_GENERATED: %s", e)
        return gen_job

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send(
        self,
        job: CampaignJob,
        worker_id: str,
        rate_per_minute: Optional[float] = None,
    ) -> Tuple[Optional[CampaignJob], Optional[RetryDecision]]:
        """
        Sends one generated job. Returns (job, retry_decision).

        `retry_decision` is populated only when the attempt failed, and carries
        whether the campaign itself should stop.
        """
        # 1. Idempotency: has this exact recipient action already been sent?
        existing = await self._db.get_idempotency_record(job.idempotency_key)
        if existing and existing.get("status") == "COMPLETED":
            logger.info(
                "Job '%s' already has a completed idempotency record; not resending.", job.id
            )
            result = existing.get("result") or {}
            updated = await self.queue.mark_already_sent(
                job,
                worker_id,
                provider=result.get("provider", "unknown"),
                provider_response=result,
                message_id=result.get("message_id"),
                thread_id=result.get("thread_id"),
            )
            return updated, None

        # 2. Dry run: generate and report, send nothing.
        if job.dry_run:
            return await self._complete_dry_run(job, worker_id), None

        # 2.5 Real-time suppression check
        from app.safety.suppression import suppression_manager
        is_sup, sup_rec = await suppression_manager.is_suppressed(job.to_email or "")
        if is_sup:
            reason_str = f"Recipient '{job.to_email}' is suppressed ({sup_rec.reason.value if sup_rec else 'SUPPRESSED'}). Outreach aborted."
            logger.warning("Suppression active for job '%s': %s", job.id, reason_str)
            updated = await self.queue.mark_failed(
                job,
                worker_id,
                reason=reason_str,
                failure_code="recipient_suppressed",
            )
            try:
                from app.campaigns.events import campaign_event_bus, CampaignEventType
                await campaign_event_bus.emit(
                    event_type=CampaignEventType.MESSAGE_FAILED,
                    campaign_id=job.campaign_id,
                    job_id=job.id,
                    worker_id=worker_id,
                    recipient_email=job.to_email,
                    details=reason_str,
                    metadata={"reason": reason_str, "failure_code": "recipient_suppressed"},
                )
            except Exception as e:
                logger.debug("Failed to emit MESSAGE_FAILED for suppressed recipient: %s", e)
            return updated, None

        # 3. Pace against the provider's quota.
        await self.limiters.acquire(job.campaign_id, rate_per_minute)

        # 4. Record the attempt before the call, so a crash mid-send is visible.
        sending = await self.queue.mark_sending(job, worker_id)
        if sending is None:
            # Lost the lease between claiming and sending: another worker owns it.
            return None, None
        job = sending

        provider = self.provider
        try:
            result: SendResult = await provider.send_email(
                to_email=job.to_email or "",
                subject=job.generated_subject or "",
                body=job.generated_body or "",
            )
        except Exception as exc:
            logger.exception("Provider raised while sending job '%s'", job.id)
            result = SendResult(
                status=SendStatus.FAILED,
                provider=getattr(provider, "name", "unknown"),
                to_email=job.to_email or "",
                subject=job.generated_subject or "",
                error=f"Provider raised: {exc}",
                error_code="provider_exception",
            )

        # 5. Only a confirmed acceptance counts as sent.
        if result.status == SendStatus.SENT:
            await self._record_idempotency(job, result)
            await self._audit_message(
                job,
                AuditEventType.MESSAGE_SENT,
                result,
                detail=f"Provider confirmed delivery to {job.to_email}.",
            )
            updated = await self.queue.mark_sent(
                job,
                worker_id,
                provider=result.provider,
                provider_status=result.status.value,
                provider_response=result.to_tool_payload(),
                message_id=result.message_id,
                thread_id=result.thread_id,
                sent_at=result.sent_at,
            )
            try:
                from app.campaigns.events import campaign_event_bus, CampaignEventType
                await campaign_event_bus.emit(
                    event_type=CampaignEventType.MESSAGE_SENT,
                    campaign_id=job.campaign_id,
                    job_id=job.id,
                    worker_id=worker_id,
                    recipient_email=job.to_email,
                    details=f"Message sent to {job.to_email} via {result.provider}. Message ID: {result.message_id or 'N/A'}",
                    metadata={"provider": result.provider, "message_id": result.message_id},
                )
            except Exception as e:
                logger.debug("Failed to emit MESSAGE_SENT: %s", e)
            return updated, None

        return await self._handle_send_failure(job, worker_id, result)

    async def _complete_dry_run(self, job: CampaignJob, worker_id: str) -> Optional[CampaignJob]:
        """
        Dry run: mark the job SKIPPED with the full preview of what would happen.

        SKIPPED rather than SENT on purpose -- a dry run must never be mistaken
        for delivery, in the job record or in the progress counters.
        """
        preview = {
            "dry_run": True,
            "would_send_to": job.to_email,
            "subject": job.generated_subject,
            "body": job.generated_body,
            "provider": getattr(self.provider, "name", "unknown"),
            "previewed_at": utc_now().isoformat(),
        }
        dry_job = await self.queue.mark_dry_run(job, worker_id, preview)
        if dry_job:
            try:
                from app.campaigns.events import campaign_event_bus, CampaignEventType
                await campaign_event_bus.emit(
                    event_type=CampaignEventType.DRY_RUN_COMPLETED,
                    campaign_id=job.campaign_id,
                    job_id=job.id,
                    worker_id=worker_id,
                    recipient_email=job.to_email,
                    details=f"Dry run simulated send to {job.to_email}: '{job.generated_subject}'",
                    metadata=preview,
                )
            except Exception as e:
                logger.debug("Failed to emit DRY_RUN_COMPLETED: %s", e)
        return dry_job

    async def _handle_send_failure(
        self, job: CampaignJob, worker_id: str, result: SendResult
    ) -> Tuple[Optional[CampaignJob], Optional[RetryDecision]]:
        decision = self.retry_policy.decide(
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            status=result.status,
            error=result.error,
            error_code=result.error_code,
            retry_after_seconds=result.retry_after_seconds,
        )

        if result.status == SendStatus.RATE_LIMITED:
            # The configured rate was too high for right now; stop issuing tokens.
            limiter = self.limiters.for_campaign(job.campaign_id)
            await limiter.penalise(result.retry_after_seconds or 30)

        # Record permanent bounce in suppression list
        if (
            result.error_code in ("mailbox_not_found", "bounce_permanent", "hard_bounce")
            or (result.error and any(b in result.error.lower() for b in ["550", "mailbox not found", "user unknown", "does not exist", "permanent bounce"]))
        ):
            try:
                from app.safety.suppression import suppression_manager, SuppressionReason
                await suppression_manager.add_suppression(
                    email=job.to_email or "",
                    reason=SuppressionReason.BOUNCE_PERMANENT,
                    campaign_id=job.campaign_id,
                    details=f"Permanent bounce reported by provider: {result.error}",
                )
            except Exception as e:
                logger.error("Failed to record bounce suppression for %s: %s", job.to_email, e)

        await self._audit_message(
            job,
            AuditEventType.MESSAGE_FAILED,
            result,
            detail=(
                f"{result.status.value}: {result.error or 'no detail'}"
                + (" (will retry)" if decision.should_retry else " (final)")
            ),
        )

        if decision.should_retry:
            updated = await self.queue.mark_retry_pending(
                job,
                worker_id,
                reason=decision.reason,
                next_attempt_at=decision.next_attempt_at(),
                provider=result.provider,
                provider_status=result.status.value,
                provider_response=result.to_tool_payload(),
                failure_code=decision.failure_code,
                retry_after_seconds=result.retry_after_seconds,
            )
            try:
                from app.campaigns.events import campaign_event_bus, CampaignEventType
                await campaign_event_bus.emit(
                    event_type=CampaignEventType.MESSAGE_RETRIED,
                    campaign_id=job.campaign_id,
                    job_id=job.id,
                    worker_id=worker_id,
                    recipient_email=job.to_email,
                    details=f"Message to {job.to_email} transiently failed ({result.error}). Retrying in {getattr(decision, 'delay_seconds', 0):.1f}s.",
                    metadata={"attempt": job.attempt_count, "failure_code": decision.failure_code},
                )
            except Exception as e:
                logger.debug("Failed to emit MESSAGE_RETRIED: %s", e)
            return updated, decision

        updated = await self.queue.mark_failed(
            job,
            worker_id,
            reason=decision.reason,
            failure_code=decision.failure_code,
            provider=result.provider,
            provider_status=result.status.value,
            provider_response=result.to_tool_payload(),
        )
        try:
            from app.campaigns.events import campaign_event_bus, CampaignEventType
            await campaign_event_bus.emit(
                event_type=CampaignEventType.MESSAGE_FAILED,
                campaign_id=job.campaign_id,
                job_id=job.id,
                worker_id=worker_id,
                recipient_email=job.to_email,
                details=f"Message to {job.to_email} permanently failed: {result.error or decision.reason}",
                metadata={"attempt": job.attempt_count, "failure_code": decision.failure_code},
            )
        except Exception as e:
            logger.debug("Failed to emit MESSAGE_FAILED: %s", e)
        return updated, decision

    async def _record_idempotency(self, job: CampaignJob, result: SendResult) -> None:
        record = IdempotencyRecord(
            idempotency_key=job.idempotency_key,
            campaign_id=job.campaign_id,
            recipient_id=job.contact_id,
            action=f"{job.channel}:{job.action}",
            status="COMPLETED",
            result=result.to_tool_payload(),
        )
        await self._db.save_idempotency_record(record.model_dump(mode="json"))


#: Shared executor instance.
campaign_job_executor = CampaignJobExecutor()
