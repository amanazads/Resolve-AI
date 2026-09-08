"""
Campaign execution worker and lifecycle service.

Two pieces:

  * CampaignWorker -- a loop that claims jobs from the persistent queue,
    generates and sends them, and stops when the campaign is no longer RUNNING.
    Several workers may run at once, in this process or in others; the queue's
    atomic claim is what keeps them off each other's jobs.

  * CampaignExecutionService -- start / pause / resume / cancel / progress, plus
    the restart recovery that makes an interrupted run resumable.

A campaign is never processed inside a FastAPI request. `start_campaign` enqueues
the jobs, records the campaign as RUNNING and returns; the work happens in a
background task, or in a separate process started with:

    python -m app.automation.worker --campaign <id>
"""

import argparse
import asyncio
import logging
import os
import signal
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.automation.executor import CampaignJobExecutor, campaign_job_executor
from app.automation.models import (
    AutomationStatus,
    CampaignJob,
    CampaignProgress,
    JobStatus,
    TERMINAL_JOB_STATUSES,
    utc_now,
)
from app.automation.queue import CampaignJobQueue, campaign_job_queue
from app.automation.rate_limiter import rate_limiters
from app.config import settings
from app.database.mongodb import db_manager
from app.permissions.middleware import enforcer
from app.permissions.models import AuditEventType
from app.permissions.service import permission_service

#: Campaign-level states. These are the same string values as
#: app.campaigns.models.CampaignStatus; AutomationStatus is used here so that the
#: execution engine does not import the campaigns package, which imports this
#: module back through its router.
CampaignStatus = AutomationStatus

logger = logging.getLogger(__name__)

DEFAULT_RATE_PER_MINUTE = 60.0
DEFAULT_CONCURRENCY = 4
DEFAULT_MAX_ATTEMPTS = 3
#: How long a worker waits between polls when nothing is claimable right now.
IDLE_POLL_SECONDS = 1.0
#: How long a worker keeps waiting for retry backoffs to elapse before giving up
#: and leaving the remaining jobs for the next worker or the next resume.
MAX_IDLE_SECONDS = 300.0


class CampaignWorker:
    """
    Processes jobs for one campaign until there is nothing claimable left, the
    campaign leaves RUNNING, or it is asked to stop.

    Concurrency is bounded: `concurrency` jobs are in flight at a time, which is
    what keeps a thousand-recipient campaign from opening a thousand sockets.
    """

    def __init__(
        self,
        campaign_id: str,
        worker_id: Optional[str] = None,
        queue: Optional[CampaignJobQueue] = None,
        executor: Optional[CampaignJobExecutor] = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        rate_per_minute: float = DEFAULT_RATE_PER_MINUTE,
        idle_poll_seconds: float = IDLE_POLL_SECONDS,
        max_idle_seconds: float = MAX_IDLE_SECONDS,
        db=None,
    ):
        self.campaign_id = campaign_id
        self.worker_id = worker_id or f"worker-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.queue = queue or campaign_job_queue
        self.executor = executor or campaign_job_executor
        self.concurrency = max(1, concurrency)
        self.rate_per_minute = rate_per_minute
        self.idle_poll_seconds = idle_poll_seconds
        self.max_idle_seconds = max_idle_seconds
        self._db = db or db_manager
        self._stop = asyncio.Event()

        self.processed = 0
        self.sent = 0
        self.failed = 0
        self.retried = 0
        self.skipped = 0

    def request_stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------

    async def run(self, campaign_context: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
        """
        Main loop. Returns a summary of what this worker did.

        Exits when the campaign is no longer RUNNING, when every job has reached
        a terminal state, when only backoff-blocked jobs remain and the worker
        has waited `max_idle_seconds` for them, or when asked to stop.
        """
        context = campaign_context or await self.load_campaign_context(self.campaign_id)
        logger.info(
            "Worker '%s' started for campaign '%s' (concurrency=%d, rate=%.0f/min).",
            self.worker_id,
            self.campaign_id,
            self.concurrency,
            self.rate_per_minute,
        )

        idle_seconds = 0.0
        in_flight: set = set()

        try:
            from app.campaigns.events import campaign_event_bus, CampaignEventType
            await campaign_event_bus.emit(
                event_type=CampaignEventType.WORKER_STATUS,
                campaign_id=self.campaign_id,
                worker_id=self.worker_id,
                details=f"Worker '{self.worker_id}' active (concurrency={self.concurrency}, rate={self.rate_per_minute}/min).",
                progress={"worker_status": "running", "worker_id": self.worker_id, "concurrency": self.concurrency},
                persist=False,
            )
        except Exception:
            pass

        try:
            while not self._stop.is_set():
                if not await self._campaign_is_running():
                    break

                # Top up to the concurrency limit.
                while len(in_flight) < self.concurrency and not self._stop.is_set():
                    job = await self.queue.claim_next(self.worker_id, campaign_id=self.campaign_id)
                    if job is None:
                        break
                    in_flight.add(asyncio.create_task(self._process(job, context)))

                if not in_flight:
                    # Nothing claimable this instant. That means either the
                    # campaign is done, or the only jobs left are inside their
                    # retry backoff and will become claimable shortly.
                    if await self._outstanding_jobs() == 0:
                        break
                    if idle_seconds >= self.max_idle_seconds:
                        logger.info(
                            "Worker '%s' giving up after %.0fs waiting on retry backoffs; "
                            "the remaining jobs stay queued for the next worker.",
                            self.worker_id,
                            idle_seconds,
                        )
                        break
                    await asyncio.sleep(self.idle_poll_seconds)
                    idle_seconds += self.idle_poll_seconds
                    continue

                idle_seconds = 0.0
                done, in_flight = await asyncio.wait(
                    in_flight, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    self._absorb(task)

            # Drain whatever is still running before reporting.
            if in_flight:
                for task in await asyncio.gather(*in_flight, return_exceptions=True):
                    pass
        finally:
            await self.sync_progress(self.campaign_id)
            try:
                from app.campaigns.events import campaign_event_bus, CampaignEventType
                await campaign_event_bus.emit(
                    event_type=CampaignEventType.WORKER_STATUS,
                    campaign_id=self.campaign_id,
                    worker_id=self.worker_id,
                    details=f"Worker '{self.worker_id}' finished. Processed: {self.processed}, Sent: {self.sent}, Failed: {self.failed}.",
                    progress={"worker_status": "idle", "worker_id": self.worker_id},
                    persist=False,
                )
            except Exception:
                pass

        summary = {
            "worker_id": self.worker_id,
            "processed": self.processed,
            "sent": self.sent,
            "failed": self.failed,
            "retried": self.retried,
            "skipped": self.skipped,
        }
        logger.info("Worker '%s' finished: %s", self.worker_id, summary)
        return summary

    @staticmethod
    def _absorb(task: asyncio.Task) -> None:
        exc = task.exception() if not task.cancelled() else None
        if exc:
            logger.exception("Campaign job task failed", exc_info=exc)

    async def _process(self, job: CampaignJob, context: Dict[str, Any]) -> None:
        """Runs one job from claimed through to a resting state."""
        self.processed += 1
        try:
            generated = await self.executor.generate(job, self.worker_id, context)
            if generated is None:
                return  # lost the lease; another worker owns it now

            if generated.status != JobStatus.READY:
                # Generation itself resolved the job (skipped or failed).
                if generated.status == JobStatus.SKIPPED:
                    self.skipped += 1
                elif generated.status == JobStatus.FAILED:
                    self.failed += 1
                return

            updated, decision = await self.executor.send(
                generated, self.worker_id, rate_per_minute=self.rate_per_minute
            )
            if updated is None:
                return

            if updated.status == JobStatus.SENT:
                self.sent += 1
            elif updated.status == JobStatus.RETRY_PENDING:
                self.retried += 1
            elif updated.status == JobStatus.SKIPPED:
                self.skipped += 1
            elif updated.status == JobStatus.FAILED:
                self.failed += 1

            if decision is not None and decision.pause_campaign:
                # e.g. the mailbox is disconnected: every remaining job would
                # fail the same way, so stop rather than burn the queue down.
                logger.error(
                    "Pausing campaign '%s': %s", self.campaign_id, decision.reason
                )
                await CampaignExecutionService(
                    queue=self.queue, db=self._db
                ).pause_campaign(
                    self.campaign_id,
                    reason=f"Paused automatically: {decision.reason}",
                )
                self.request_stop()

        except Exception as exc:
            logger.exception("Unhandled error processing job '%s'", job.id)
            await self.queue.mark_failed(
                job,
                self.worker_id,
                reason=f"Worker error: {exc}",
                failure_code="worker_error",
            )
            self.failed += 1

    async def _outstanding_jobs(self) -> int:
        """Jobs that are neither terminal nor currently held by a worker."""
        counts = await self.queue.status_counts(self.campaign_id)
        return sum(
            int(counts.get(status.value, 0))
            for status in (JobStatus.PENDING, JobStatus.READY, JobStatus.RETRY_PENDING)
        )

    async def _campaign_is_running(self) -> bool:
        doc = await self._db.get_campaign(self.campaign_id)
        if not doc:
            logger.warning("Campaign '%s' disappeared; stopping worker.", self.campaign_id)
            return False
        status = doc.get("status")
        if status != CampaignStatus.RUNNING.value:
            logger.info(
                "Worker '%s' stopping: campaign '%s' is %s.",
                self.worker_id,
                self.campaign_id,
                status,
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Shared helpers, also used by the service
    # ------------------------------------------------------------------

    @staticmethod
    async def load_campaign_context(campaign_id: str, db=None) -> Dict[str, Any]:
        """Everything the generator needs, read once per worker rather than per job."""
        database = db or db_manager
        doc = await database.get_campaign(campaign_id) or {}
        execution = doc.get("execution") or {}
        plan = doc.get("plan") or {}

        return {
            "objective": doc.get("objective") or doc.get("goal") or "",
            "campaign_type": plan.get("campaign_type") or doc.get("campaign_type") or "CUSTOM_OUTREACH",
            "instructions": doc.get("message_strategy") or "",
            "personalization_fields": doc.get("personalization_fields") or None,
            "tone": plan.get("tone") or "professional",
            "sender_profile": execution.get("sender_profile") or {},
            "startup_info": execution.get("startup_info") or {},
        }

    async def sync_progress(self, campaign_id: str) -> CampaignProgress:
        return await CampaignExecutionService(queue=self.queue, db=self._db).sync_progress(
            campaign_id
        )


class CampaignExecutionService:
    """
    Campaign lifecycle: enqueue, start, pause, resume, cancel, progress, recover.

    Holds the background worker tasks for this process. Workers running in other
    processes are invisible here, and that is fine: coordination happens through
    the campaign status and the job leases in MongoDB, not through this object.
    """

    def __init__(
        self,
        queue: Optional[CampaignJobQueue] = None,
        executor: Optional[CampaignJobExecutor] = None,
        db=None,
    ):
        self.queue = queue or campaign_job_queue
        self.executor = executor or campaign_job_executor
        self._db = db or db_manager

    #: campaign_id -> running worker task, for this process only.
    _tasks: Dict[str, asyncio.Task] = {}
    _workers: Dict[str, CampaignWorker] = {}

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    async def load_audience(self, campaign: Dict[str, Any], limit: int = 10000) -> List[Dict[str, Any]]:
        """
        Resolves the campaign's audience to contacts.

        Paginates rather than asking for everything at once, so a campaign with
        thousands of recipients does not materialise as one enormous query.
        """
        dataset_id = campaign.get("dataset_id")
        if not dataset_id:
            return []

        audience = campaign.get("audience") or []
        contacts: List[Dict[str, Any]] = []
        seen: set = set()
        page_size = 500

        # An empty audience means "everyone valid in the dataset".
        audience_filters: List[Dict[str, Any]] = (
            [{"dataset_id": dataset_id, "contact_type": t, "is_valid": True} for t in audience]
            if audience
            else [{"dataset_id": dataset_id, "is_valid": True}]
        )

        for query in audience_filters:
            skip = 0
            while len(contacts) < limit:
                page = await self._db.query_contacts(query, skip=skip, limit=page_size)
                if not page:
                    break
                for contact in page:
                    key = contact.get("contact_id") or contact.get("email")
                    if key and key not in seen:
                        seen.add(key)
                        contacts.append(contact)
                if len(page) < page_size:
                    break
                skip += page_size

        return contacts[:limit]

    async def enqueue_campaign(
        self,
        campaign_id: str,
        channel: str = "EMAIL",
        action: str = "SEND_MESSAGE",
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        dry_run: bool = False,
        contacts: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """Creates the persistent jobs for a campaign. Safe to call more than once."""
        campaign = await self._db.get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign '{campaign_id}' not found.")

        audience = contacts if contacts is not None else await self.load_audience(campaign)
        jobs = self.queue.build_jobs(
            campaign_id=campaign_id,
            contacts=audience,
            channel=channel,
            action=action,
            max_attempts=max_attempts,
            dry_run=dry_run,
        )
        return await self.queue.enqueue(jobs)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_campaign(
        self,
        campaign_id: str,
        dry_run: bool = False,
        rate_per_minute: float = DEFAULT_RATE_PER_MINUTE,
        concurrency: int = DEFAULT_CONCURRENCY,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        sender_profile: Optional[Dict[str, Any]] = None,
        startup_info: Optional[Dict[str, Any]] = None,
        contacts: Optional[List[Dict[str, Any]]] = None,
        run_in_background: bool = True,
        principal_user_id: Optional[str] = None,
        allow_direct_execution: bool = False,
        campaign_limits: Optional[Any] = None,
        validate_safety: bool = True,
    ) -> CampaignProgress:
        """
        Enqueues the campaign and starts a worker.

        Returns as soon as the jobs are persisted; the sending happens in the
        background -- no campaign is processed inside the request that started it.

        When `principal_user_id` is given, the run is checked against that user's
        stored permission grants **once, here**. Every recipient in the campaign
        then proceeds without asking again; a campaign with a different audience,
        dataset or mailbox produces a different check that an existing grant will
        not match. Callers that represent a user (the API, the automation agent)
        always pass it.
        """
        campaign = await self._db.get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign '{campaign_id}' not found.")

        status = campaign.get("status")
        if status == CampaignStatus.CANCELLED.value:
            raise ValueError("A cancelled campaign cannot be started. Create a new one.")
        if campaign_id in self._tasks and not self._tasks[campaign_id].done():
            logger.info("Campaign '%s' already has a worker in this process.", campaign_id)
            return await self.get_progress(campaign_id)

        provider_name = (campaign.get("execution") or {}).get("provider")
        if not provider_name:
            from app.integrations.registry import get_email_provider
            try:
                active_p = get_email_provider()
                provider_name = getattr(active_p, "name", None) or settings.EMAIL_PROVIDER
            except Exception:
                provider_name = settings.EMAIL_PROVIDER

        # Pre-campaign Safety & Compliance Validation
        if validate_safety:
            from app.safety import CampaignSafetyValidator, CampaignLimits, SuppressionManager, SafetyAuditLogger
            suppression_svc = SuppressionManager(db=self._db)
            audit_svc = SafetyAuditLogger(db=self._db)
            validator = CampaignSafetyValidator(suppression_svc=suppression_svc, audit_svc=audit_svc)

            audience_to_validate = contacts if contacts is not None else await self.load_audience(campaign)
            status_check = await enforcer.integration_status(provider_name)

            acc_id = status_check.get("account") or "default"
            account_record = await self._db.get_integration_account(provider_name, acc_id)
            is_new_integration = (
                (account_record is not None and account_record.get("direct_campaigns_count", 0) == 0)
                or (campaign.get("metadata", {}).get("is_new_integration", False))
            )

            limits_obj = (
                CampaignLimits(**campaign_limits)
                if isinstance(campaign_limits, dict)
                else (campaign_limits or CampaignLimits())
            )
            effective_sender = sender_profile or (campaign.get("execution") or {}).get("sender_profile") or {
                "name": principal_user_id or campaign.get("owner_id") or "Campaign Operator",
                "email": status_check.get("account") or "operator@resolve.ai",
            }

            validation_report = await validator.validate_campaign(
                campaign_id=campaign_id,
                recipients=audience_to_validate,
                campaign_purpose=campaign.get("objective") or campaign.get("goal") or campaign.get("name") or "Outreach Campaign",
                sender_profile=effective_sender,
                provider=provider_name,
                integration_status=status_check,
                limits=limits_obj,
                dry_run=dry_run,
                allow_direct_execution=allow_direct_execution,
                is_new_integration=is_new_integration,
                actor_id=principal_user_id or campaign.get("owner_id"),
                strict=True,
            )
            dry_run = validation_report.effective_dry_run

        if principal_user_id:
            audience = campaign.get("audience") or []
            recipient_count = (
                len(contacts) if contacts is not None else int(campaign.get("total_contacts") or 0)
            )
            # One check for the whole campaign, before a single job is created.
            await enforcer.require_campaign_send(
                user_id=principal_user_id,
                campaign_id=campaign_id,
                integration=provider_name,
                dataset_id=campaign.get("dataset_id"),
                audience=audience,
                recipient_count=recipient_count,
                dry_run=dry_run,
            )

        await self.enqueue_campaign(
            campaign_id,
            max_attempts=max_attempts,
            dry_run=dry_run,
            contacts=contacts,
        )

        execution = dict(campaign.get("execution") or {})
        execution.update(
            {
                "dry_run": dry_run,
                "rate_per_minute": rate_per_minute,
                "concurrency": concurrency,
                "max_attempts": max_attempts,
                "started_at": utc_now().isoformat(),
            }
        )
        if sender_profile is not None:
            execution["sender_profile"] = sender_profile
        if startup_info is not None:
            execution["startup_info"] = startup_info

        await self._db.update_campaign(
            campaign_id,
            {
                "status": CampaignStatus.RUNNING.value,
                "execution": execution,
                "updated_at": utc_now().isoformat(),
            },
        )

        progress = await self.get_progress(campaign_id)
        await permission_service.audit(
            AuditEventType.CAMPAIGN_STARTED,
            user_id=principal_user_id or campaign.get("owner_id"),
            actor=principal_user_id or "system",
            integration=provider_name,
            campaign_id=campaign_id,
            dataset_id=campaign.get("dataset_id"),
            outcome="DRY_RUN" if dry_run else "RUNNING",
            detail=(
                f"Campaign started with {progress.total} job(s) at "
                f"{rate_per_minute}/min"
                + (" (dry run: nothing will be sent)." if dry_run else ".")
            ),
            metadata={
                "audience": campaign.get("audience") or [],
                "concurrency": concurrency,
                "max_attempts": max_attempts,
            },
        )

        try:
            from app.campaigns.events import campaign_event_bus, CampaignEventType
            await campaign_event_bus.emit(
                event_type=CampaignEventType.CAMPAIGN_STARTED,
                campaign_id=campaign_id,
                details=(
                    f"Campaign started with {progress.total} recipient job(s) at {rate_per_minute}/min"
                    + (" (dry run mode)." if dry_run else ".")
                ),
                progress={
                    "total": progress.total,
                    "completed": progress.completed,
                    "sent": progress.sent,
                    "failed": progress.failed,
                    "retry_pending": progress.retry_pending,
                    "percent_complete": progress.percent_complete,
                    "status": "RUNNING",
                    "worker_status": "starting",
                    "dry_run": dry_run,
                },
                metadata={
                    "concurrency": concurrency,
                    "rate_per_minute": rate_per_minute,
                    "dry_run": dry_run,
                    "provider": provider_name,
                },
            )
        except Exception as exc:
            logger.warning("Failed to emit CAMPAIGN_STARTED event: %s", exc)

        await self._spawn_worker(
            campaign_id,
            rate_per_minute=rate_per_minute,
            concurrency=concurrency,
            run_in_background=run_in_background,
        )
        return await self.get_progress(campaign_id)

    async def _spawn_worker(
        self,
        campaign_id: str,
        rate_per_minute: float,
        concurrency: int,
        run_in_background: bool = True,
    ) -> None:
        worker = CampaignWorker(
            campaign_id=campaign_id,
            queue=self.queue,
            executor=self.executor,
            concurrency=concurrency,
            rate_per_minute=rate_per_minute,
            db=self._db,
        )
        self._workers[campaign_id] = worker
        context = await CampaignWorker.load_campaign_context(campaign_id, db=self._db)

        if not run_in_background:
            try:
                await worker.run(context)
            finally:
                self._workers.pop(campaign_id, None)
            return

        async def _run():
            try:
                await worker.run(context)
            finally:
                self._workers.pop(campaign_id, None)
                self._tasks.pop(campaign_id, None)

        self._tasks[campaign_id] = asyncio.create_task(_run())

    async def pause_campaign(
        self, campaign_id: str, reason: Optional[str] = None
    ) -> CampaignProgress:
        """
        Pauses a campaign. In-flight jobs finish; nothing new is claimed.

        A job already handed to the provider is deliberately not interrupted --
        stopping mid-call is exactly how a duplicate gets created.
        """
        updates: Dict[str, Any] = {
            "status": CampaignStatus.PAUSED.value,
            "updated_at": utc_now().isoformat(),
        }
        if reason:
            campaign = await self._db.get_campaign(campaign_id) or {}
            execution = dict(campaign.get("execution") or {})
            execution["pause_reason"] = reason
            updates["execution"] = execution

        await self._db.update_campaign(campaign_id, updates)

        worker = self._workers.get(campaign_id)
        if worker:
            worker.request_stop()

        logger.info("Campaign '%s' paused%s.", campaign_id, f": {reason}" if reason else "")
        try:
            from app.campaigns.events import campaign_event_bus, CampaignEventType
            await campaign_event_bus.emit(
                event_type=CampaignEventType.CAMPAIGN_PAUSED,
                campaign_id=campaign_id,
                details=f"Campaign paused{f': {reason}' if reason else ''}.",
                progress={"status": "PAUSED", "worker_status": "paused"},
                metadata={"reason": reason},
            )
        except Exception as exc:
            logger.warning("Failed to emit CAMPAIGN_PAUSED event: %s", exc)
        return await self.sync_progress(campaign_id)

    async def resume_campaign(
        self,
        campaign_id: str,
        rate_per_minute: Optional[float] = None,
        concurrency: Optional[int] = None,
        run_in_background: bool = True,
        principal_user_id: Optional[str] = None,
    ) -> CampaignProgress:
        """
        Resumes a paused campaign, picking up exactly where it stopped.

        Jobs already SENT stay SENT and are not re-claimed, so resuming never
        re-sends anything.
        """
        campaign = await self._db.get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign '{campaign_id}' not found.")
        if campaign.get("status") == CampaignStatus.CANCELLED.value:
            raise ValueError("A cancelled campaign cannot be resumed.")

        execution = dict(campaign.get("execution") or {})
        execution.pop("pause_reason", None)
        execution["resumed_at"] = utc_now().isoformat()

        await self._db.update_campaign(
            campaign_id,
            {
                "status": CampaignStatus.RUNNING.value,
                "execution": execution,
                "updated_at": utc_now().isoformat(),
            },
        )

        if principal_user_id:
            # Resuming is still sending, so it is checked the same way. A grant
            # revoked while the campaign was paused stops it here.
            resumed_provider = (campaign.get("execution") or {}).get("provider")
            if not resumed_provider:
                from app.integrations.registry import get_email_provider
                try:
                    active_p = get_email_provider()
                    resumed_provider = getattr(active_p, "name", None) or settings.EMAIL_PROVIDER
                except Exception:
                    resumed_provider = settings.EMAIL_PROVIDER

            await enforcer.require_campaign_send(
                user_id=principal_user_id,
                campaign_id=campaign_id,
                integration=resumed_provider,
                dataset_id=campaign.get("dataset_id"),
                audience=campaign.get("audience") or [],
                recipient_count=int(campaign.get("total_contacts") or 0),
                dry_run=bool(execution.get("dry_run")),
            )

        # Anything a dead worker was holding comes back before we start.
        await self.queue.recover_expired_leases()

        await self._spawn_worker(
            campaign_id,
            rate_per_minute=rate_per_minute or execution.get("rate_per_minute", DEFAULT_RATE_PER_MINUTE),
            concurrency=concurrency or execution.get("concurrency", DEFAULT_CONCURRENCY),
            run_in_background=run_in_background,
        )
        try:
            from app.campaigns.events import campaign_event_bus, CampaignEventType
            await campaign_event_bus.emit(
                event_type=CampaignEventType.CAMPAIGN_RESUMED,
                campaign_id=campaign_id,
                details="Campaign execution resumed.",
                progress={"status": "RUNNING", "worker_status": "resumed"},
            )
        except Exception as exc:
            logger.warning("Failed to emit CAMPAIGN_RESUMED event: %s", exc)
        return await self.get_progress(campaign_id)

    async def cancel_campaign(self, campaign_id: str) -> CampaignProgress:
        """Cancels the campaign and every job that has not already finished."""
        await self._db.update_campaign(
            campaign_id,
            {"status": CampaignStatus.CANCELLED.value, "updated_at": utc_now().isoformat()},
        )

        worker = self._workers.get(campaign_id)
        if worker:
            worker.request_stop()

        await self.queue.cancel_campaign_jobs(campaign_id)
        rate_limiters.release_campaign(campaign_id)
        logger.info("Campaign '%s' cancelled.", campaign_id)
        try:
            from app.campaigns.events import campaign_event_bus, CampaignEventType
            await campaign_event_bus.emit(
                event_type=CampaignEventType.CAMPAIGN_CANCELLED,
                campaign_id=campaign_id,
                details="Campaign cancelled. Pending jobs terminated.",
                progress={"status": "CANCELLED", "worker_status": "idle"},
            )
        except Exception as exc:
            logger.warning("Failed to emit CAMPAIGN_CANCELLED event: %s", exc)
        return await self.sync_progress(campaign_id)

    # ------------------------------------------------------------------
    # Progress
    # ------------------------------------------------------------------

    async def sync_progress(self, campaign_id: str) -> CampaignProgress:
        """
        Recomputes progress from the jobs and persists it.

        Progress lives in its own collection rather than only in memory, so it
        survives a restart and can be read by a process that is not running the
        campaign.
        """
        counts = await self.queue.status_counts(campaign_id)
        campaign = await self._db.get_campaign(campaign_id) or {}
        execution = campaign.get("execution") or {}

        def count(status: JobStatus) -> int:
            return int(counts.get(status.value, 0))

        total = sum(int(v) for v in counts.values())
        completed = sum(count(s) for s in TERMINAL_JOB_STATUSES)
        in_flight = count(JobStatus.GENERATING) + count(JobStatus.SENDING)
        remaining = total - completed

        progress = CampaignProgress(
            campaign_id=campaign_id,
            status=campaign.get("status", CampaignStatus.READY.value),
            dry_run=bool(execution.get("dry_run")),
            total=total,
            pending=count(JobStatus.PENDING),
            generating=count(JobStatus.GENERATING),
            ready=count(JobStatus.READY),
            sending=count(JobStatus.SENDING),
            sent=count(JobStatus.SENT),
            failed=count(JobStatus.FAILED),
            retry_pending=count(JobStatus.RETRY_PENDING),
            skipped=count(JobStatus.SKIPPED),
            cancelled=count(JobStatus.CANCELLED),
            requires_manual_review=await self.queue.review_count(campaign_id),
            in_flight=in_flight,
            completed=completed,
            remaining=remaining,
            percent_complete=round(completed / total * 100, 2) if total else 0.0,
            started_at=_parse_dt(execution.get("started_at")),
            last_error=execution.get("pause_reason"),
        )

        # A campaign whose jobs are all terminal is finished.
        campaign_status = campaign.get("status")
        if (
            total > 0
            and remaining == 0
            and campaign_status == CampaignStatus.RUNNING.value
        ):
            new_status = (
                CampaignStatus.FAILED.value
                if progress.sent == 0 and progress.failed > 0
                else CampaignStatus.COMPLETED.value
            )
            progress.status = new_status
            progress.finished_at = utc_now()
            await self._db.update_campaign(
                campaign_id,
                {"status": new_status, "updated_at": utc_now().isoformat()},
            )

        # Keep the campaign document's own counters in step for existing readers.
        await self._db.update_campaign(
            campaign_id,
            {
                "total_contacts": total,
                "sent": progress.sent,
                "failed": progress.failed,
                "pending": progress.pending + progress.retry_pending,
                "generated": progress.ready + progress.sent,
            },
        )

        await self._db.save_campaign_progress(progress.model_dump(mode="json"))

        try:
            from app.campaigns.events import campaign_event_bus, CampaignEventType
            worker_active = campaign_id in self._tasks and not self._tasks[campaign_id].done()
            prog_dict = {
                "total": progress.total,
                "completed": progress.completed,
                "sent": progress.sent,
                "failed": progress.failed,
                "retry_pending": progress.retry_pending,
                "generating": progress.generating,
                "ready": progress.ready,
                "skipped": progress.skipped,
                "percent_complete": progress.percent_complete,
                "status": progress.status,
                "worker_status": "running" if worker_active else ("paused" if progress.status == "PAUSED" else "idle"),
            }
            if total > 0 and remaining == 0 and campaign_status == CampaignStatus.RUNNING.value:
                await campaign_event_bus.emit(
                    event_type=CampaignEventType.CAMPAIGN_COMPLETED,
                    campaign_id=campaign_id,
                    details=f"Campaign execution completed. Total sent: {progress.sent}, failed: {progress.failed}.",
                    progress=prog_dict,
                )
            else:
                await campaign_event_bus.emit(
                    event_type=CampaignEventType.PROGRESS_UPDATED,
                    campaign_id=campaign_id,
                    details=f"Progress: {progress.completed}/{progress.total} ({progress.percent_complete}%)",
                    progress=prog_dict,
                    persist=False,
                )
        except Exception as exc:
            logger.debug("Failed to emit progress event: %s", exc)

        return progress

    async def get_progress(self, campaign_id: str) -> CampaignProgress:
        return await self.sync_progress(campaign_id)

    async def list_jobs(
        self,
        campaign_id: str,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[CampaignJob]:
        return await self.queue.list_jobs(campaign_id, status=status, skip=skip, limit=limit)

    # ------------------------------------------------------------------
    # Restart recovery
    # ------------------------------------------------------------------

    async def recover_on_startup(self, resume_running: bool = True) -> Dict[str, int]:
        """
        Called at application startup.

        Reclaims jobs orphaned by workers that died, then restarts a worker for
        every campaign still marked RUNNING so an interrupted run continues
        instead of stalling forever.
        """
        requeued, flagged = await self.queue.recover_expired_leases()

        resumed = 0
        campaigns = await self._db.list_campaigns()
        for campaign in campaigns:
            if campaign.get("status") != CampaignStatus.RUNNING.value:
                continue
            campaign_id = campaign.get("campaign_id") or campaign.get("id")
            if not campaign_id:
                continue

            progress = await self.sync_progress(campaign_id)
            if progress.remaining <= 0:
                continue
            if not resume_running:
                continue

            execution = campaign.get("execution") or {}
            await self._spawn_worker(
                campaign_id,
                rate_per_minute=execution.get("rate_per_minute", DEFAULT_RATE_PER_MINUTE),
                concurrency=execution.get("concurrency", DEFAULT_CONCURRENCY),
                run_in_background=True,
            )
            resumed += 1

        summary = {"requeued": requeued, "flagged_for_review": flagged, "campaigns_resumed": resumed}
        logger.info("Campaign execution recovery: %s", summary)
        return summary

    async def shutdown(self) -> None:
        """Asks every worker in this process to stop, and waits for them."""
        for worker in list(self._workers.values()):
            worker.request_stop()
        tasks = [task for task in self._tasks.values() if not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._workers.clear()


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


#: Shared service instance used by the API layer.
campaign_execution_service = CampaignExecutionService()


# ==========================================================================
# Standalone worker process
# ==========================================================================


async def _run_standalone(campaign_id: Optional[str], concurrency: int, rate: float) -> None:
    await db_manager.connect()
    service = CampaignExecutionService()
    worker: Optional[CampaignWorker] = None

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):  # pragma: no cover - platform dependent
            pass

    try:
        if campaign_id:
            await service.queue.recover_expired_leases()
            worker = CampaignWorker(
                campaign_id=campaign_id, concurrency=concurrency, rate_per_minute=rate
            )
            runner = asyncio.create_task(worker.run())
            await asyncio.wait([runner, asyncio.create_task(stop_event.wait())],
                               return_when=asyncio.FIRST_COMPLETED)
            if not runner.done():
                worker.request_stop()
                await runner
        else:
            await service.recover_on_startup()
            await stop_event.wait()
            await service.shutdown()
    finally:
        await db_manager.close()


def main() -> None:  # pragma: no cover - process entrypoint
    parser = argparse.ArgumentParser(description="Resolve AI campaign execution worker.")
    parser.add_argument("--campaign", help="Process one campaign, then exit.")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE_PER_MINUTE)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(_run_standalone(args.campaign, args.concurrency, args.rate))


if __name__ == "__main__":  # pragma: no cover
    main()
