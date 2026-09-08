"""
Persistent campaign job queue, backed by the existing MongoDB infrastructure.

Every state transition goes through this module, and every transition that a
worker makes while holding a job is a compare-and-set against that worker's
lease. Two workers can therefore never drive the same job, and a worker whose
lease expired cannot overwrite the state of the worker that took over.

No in-memory queue is kept: the collection *is* the queue, which is what makes a
restart resume rather than restart.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.automation.models import (
    CLAIMABLE_JOB_STATUSES,
    IN_FLIGHT_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    CampaignJob,
    JobAttempt,
    JobStatus,
    utc_now,
)
from app.database.mongodb import db_manager

logger = logging.getLogger(__name__)

DEFAULT_LEASE_SECONDS = 120


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _serialize(job: CampaignJob) -> Dict[str, Any]:
    """Datetimes as ISO strings, so Mongo and the in-memory store compare alike."""
    return job.model_dump(mode="json")


class CampaignJobQueue:
    """Enqueue, claim, complete, retry and cancel campaign jobs."""

    def __init__(self, db=None, lease_seconds: int = DEFAULT_LEASE_SECONDS):
        self._db = db or db_manager
        self.lease_seconds = lease_seconds

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    @staticmethod
    def build_jobs(
        campaign_id: str,
        contacts: List[Dict[str, Any]],
        channel: str = "EMAIL",
        action: str = "SEND_MESSAGE",
        max_attempts: int = 3,
        dry_run: bool = False,
    ) -> List[CampaignJob]:
        """
        One job per recipient action, each carrying a snapshot of the contact.

        A contact with no address becomes a SKIPPED job rather than being dropped:
        the campaign's totals should account for everyone in the audience.
        """
        jobs: List[CampaignJob] = []
        for contact in contacts:
            contact_id = str(
                contact.get("contact_id") or contact.get("id") or contact.get("email") or ""
            ).strip()
            if not contact_id:
                continue

            email = (contact.get("email") or "").strip()
            job = CampaignJob(
                campaign_id=campaign_id,
                contact_id=contact_id,
                channel=channel,
                action=action,
                idempotency_key=CampaignJob.build_idempotency_key(
                    campaign_id, contact_id, channel, action
                ),
                recipient=dict(contact),
                to_email=email or None,
                max_attempts=max_attempts,
                dry_run=dry_run,
            )
            if not email:
                job.status = JobStatus.SKIPPED
                job.failure_reason = "Contact has no email address."
                job.failure_code = "missing_email"
                job.completed_at = utc_now()
            jobs.append(job)
        return jobs

    async def enqueue(self, jobs: List[CampaignJob]) -> int:
        """
        Persists jobs, skipping any whose idempotency key already exists.

        Re-running enqueue for a campaign is therefore safe: it tops up newly
        added recipients and leaves existing jobs, including completed ones,
        exactly as they are.
        """
        if not jobs:
            return 0
        inserted = await self._db.insert_campaign_jobs([_serialize(job) for job in jobs])
        logger.info(
            "Enqueued %d of %d campaign jobs (%d already existed).",
            inserted,
            len(jobs),
            len(jobs) - inserted,
        )
        return inserted

    # ------------------------------------------------------------------
    # Claim
    # ------------------------------------------------------------------

    async def claim_next(
        self,
        worker_id: str,
        campaign_id: Optional[str] = None,
        lease_seconds: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> Optional[CampaignJob]:
        """
        Atomically leases the next claimable job and moves it to GENERATING.

        Returns None when nothing is claimable right now -- which includes jobs
        that exist but are still inside their retry backoff.
        """
        now = now or utc_now()
        lease = lease_seconds or self.lease_seconds
        doc = await self._db.claim_next_campaign_job(
            worker_id=worker_id,
            claimable_statuses=[s.value for s in CLAIMABLE_JOB_STATUSES],
            in_flight_status=JobStatus.GENERATING.value,
            now_iso=now.isoformat(),
            lease_expires_iso=(now + timedelta(seconds=lease)).isoformat(),
            campaign_id=campaign_id,
        )
        if not doc:
            return None
        return CampaignJob.model_validate(doc)

    async def renew_lease(
        self, job: CampaignJob, worker_id: str, lease_seconds: Optional[int] = None
    ) -> bool:
        now = utc_now()
        lease = lease_seconds or self.lease_seconds
        updated = await self._db.update_campaign_job(
            job.id,
            {
                "lease_expires_at": (now + timedelta(seconds=lease)).isoformat(),
                "updated_at": now.isoformat(),
            },
            expected_lease_owner=worker_id,
        )
        return updated is not None

    # ------------------------------------------------------------------
    # Transitions taken while holding the lease
    # ------------------------------------------------------------------

    async def _transition(
        self,
        job: CampaignJob,
        worker_id: Optional[str],
        updates: Dict[str, Any],
        expected_status: Optional[JobStatus] = None,
    ) -> Optional[CampaignJob]:
        updates = dict(updates)
        updates["updated_at"] = utc_now().isoformat()
        doc = await self._db.update_campaign_job(
            job.id,
            updates,
            expected_status=expected_status.value if expected_status else None,
            expected_lease_owner=worker_id,
        )
        if doc is None:
            logger.warning(
                "Job '%s' transition rejected: it is no longer held by worker '%s'.",
                job.id,
                worker_id,
            )
            return None
        return CampaignJob.model_validate(doc)

    async def mark_generated(
        self,
        job: CampaignJob,
        worker_id: str,
        subject: str,
        body: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[CampaignJob]:
        """GENERATING -> READY, storing the generated message."""
        return await self._transition(
            job,
            worker_id,
            {
                "status": JobStatus.READY.value,
                "generated_subject": subject,
                "generated_body": body,
                "generation_metadata": metadata or {},
                "generated_at": utc_now().isoformat(),
            },
            expected_status=JobStatus.GENERATING,
        )

    async def mark_sending(self, job: CampaignJob, worker_id: str) -> Optional[CampaignJob]:
        """
        READY -> SENDING, recorded *before* the provider is called.

        This is what makes an interrupted send visible after a crash: a job found
        in SENDING with a dead lease may or may not have been delivered, and is
        never blindly retried.
        """
        return await self._transition(
            job,
            worker_id,
            {
                "status": JobStatus.SENDING.value,
                "attempt_count": job.attempt_count + 1,
            },
            expected_status=JobStatus.READY,
        )

    async def mark_sent(
        self,
        job: CampaignJob,
        worker_id: str,
        provider: str,
        provider_status: str,
        provider_response: Dict[str, Any],
        message_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        sent_at: Optional[datetime] = None,
    ) -> Optional[CampaignJob]:
        now = utc_now()
        attempt = JobAttempt(
            attempt=job.attempt_count,
            status=JobStatus.SENT.value,
            worker_id=worker_id,
            provider=provider,
            provider_status=provider_status,
            message_id=message_id,
        )
        return await self._transition(
            job,
            worker_id,
            {
                "status": JobStatus.SENT.value,
                "provider": provider,
                "provider_status": provider_status,
                "provider_response": provider_response,
                "message_id": message_id,
                "thread_id": thread_id,
                "sent_at": _iso(sent_at or now),
                "completed_at": now.isoformat(),
                "failure_reason": None,
                "failure_code": None,
                "lease_owner": None,
                "lease_expires_at": None,
                "attempts": [a.model_dump(mode="json") for a in job.attempts + [attempt]],
            },
            expected_status=JobStatus.SENDING,
        )

    async def mark_dry_run(
        self, job: CampaignJob, worker_id: str, preview: Dict[str, Any]
    ) -> Optional[CampaignJob]:
        """
        READY -> SKIPPED with the full preview of what would have been sent.

        SKIPPED rather than SENT deliberately: a dry run must never be counted as
        a delivery, in the job record or in the progress totals. The attempt
        count is left alone, because nothing was attempted.
        """
        return await self._transition(
            job,
            worker_id,
            {
                "status": JobStatus.SKIPPED.value,
                "provider_status": "DRY_RUN",
                "provider_response": preview,
                "failure_reason": None,
                "failure_code": "dry_run",
                "completed_at": utc_now().isoformat(),
                "lease_owner": None,
                "lease_expires_at": None,
            },
            expected_status=JobStatus.READY,
        )

    async def mark_already_sent(
        self,
        job: CampaignJob,
        worker_id: str,
        provider: str,
        provider_response: Dict[str, Any],
        message_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> Optional[CampaignJob]:
        """
        READY -> SENT for a job whose recipient action already completed.

        Reached when a completed idempotency record exists for this job's key:
        the message went out on an earlier run, so the job is reconciled to SENT
        without another provider call and without counting a new attempt.
        """
        now = utc_now()
        return await self._transition(
            job,
            worker_id,
            {
                "status": JobStatus.SENT.value,
                "provider": provider,
                "provider_status": JobStatus.SENT.value,
                "provider_response": {**provider_response, "deduplicated": True},
                "message_id": message_id,
                "thread_id": thread_id,
                "sent_at": provider_response.get("sent_at") or now.isoformat(),
                "completed_at": now.isoformat(),
                "failure_reason": None,
                "failure_code": None,
                "lease_owner": None,
                "lease_expires_at": None,
            },
            expected_status=JobStatus.READY,
        )

    async def mark_retry_pending(
        self,
        job: CampaignJob,
        worker_id: str,
        reason: str,
        next_attempt_at: datetime,
        provider: Optional[str] = None,
        provider_status: Optional[str] = None,
        provider_response: Optional[Dict[str, Any]] = None,
        failure_code: Optional[str] = None,
        retry_after_seconds: Optional[int] = None,
    ) -> Optional[CampaignJob]:
        attempt = JobAttempt(
            attempt=job.attempt_count,
            status=JobStatus.RETRY_PENDING.value,
            worker_id=worker_id,
            provider=provider,
            provider_status=provider_status,
            error=reason,
            error_code=failure_code,
            retry_after_seconds=retry_after_seconds,
        )
        return await self._transition(
            job,
            worker_id,
            {
                "status": JobStatus.RETRY_PENDING.value,
                "failure_reason": reason,
                "failure_code": failure_code,
                "provider": provider or job.provider,
                "provider_status": provider_status,
                "provider_response": provider_response or {},
                "next_attempt_at": next_attempt_at.isoformat(),
                "lease_owner": None,
                "lease_expires_at": None,
                "attempts": [a.model_dump(mode="json") for a in job.attempts + [attempt]],
            },
        )

    async def mark_failed(
        self,
        job: CampaignJob,
        worker_id: Optional[str],
        reason: str,
        failure_code: Optional[str] = None,
        provider: Optional[str] = None,
        provider_status: Optional[str] = None,
        provider_response: Optional[Dict[str, Any]] = None,
        requires_manual_review: bool = False,
    ) -> Optional[CampaignJob]:
        now = utc_now()
        attempt = JobAttempt(
            attempt=job.attempt_count,
            status=JobStatus.FAILED.value,
            worker_id=worker_id,
            provider=provider,
            provider_status=provider_status,
            error=reason,
            error_code=failure_code,
        )
        return await self._transition(
            job,
            worker_id,
            {
                "status": JobStatus.FAILED.value,
                "failure_reason": reason,
                "failure_code": failure_code,
                "permanent_failure": True,
                "requires_manual_review": requires_manual_review,
                "provider": provider or job.provider,
                "provider_status": provider_status,
                "provider_response": provider_response or {},
                "completed_at": now.isoformat(),
                "next_attempt_at": None,
                "lease_owner": None,
                "lease_expires_at": None,
                "attempts": [a.model_dump(mode="json") for a in job.attempts + [attempt]],
            },
        )

    async def mark_skipped(
        self, job: CampaignJob, worker_id: Optional[str], reason: str, failure_code: str = "skipped"
    ) -> Optional[CampaignJob]:
        return await self._transition(
            job,
            worker_id,
            {
                "status": JobStatus.SKIPPED.value,
                "failure_reason": reason,
                "failure_code": failure_code,
                "completed_at": utc_now().isoformat(),
                "lease_owner": None,
                "lease_expires_at": None,
            },
        )

    async def release(self, job: CampaignJob, worker_id: str, status: JobStatus) -> Optional[CampaignJob]:
        """Hands a held job back to the queue, e.g. when the campaign is paused."""
        return await self._transition(
            job,
            worker_id,
            {
                "status": status.value,
                "lease_owner": None,
                "lease_expires_at": None,
            },
        )

    # ------------------------------------------------------------------
    # Recovery and bulk operations
    # ------------------------------------------------------------------

    async def recover_expired_leases(self, now: Optional[datetime] = None) -> Tuple[int, int]:
        """
        Reclaims jobs whose worker died.

        A job interrupted while GENERATING never reached a provider, so it goes
        straight back to PENDING. A job interrupted while SENDING may or may not
        have been delivered -- there is no way to know from here -- so it is
        never re-sent. It is failed and flagged for review instead, because a
        duplicate email is worse than a missing one.

        Returns (requeued, flagged_for_review).
        """
        now = now or utc_now()
        stale = await self._db.find_expired_campaign_job_leases(
            now_iso=now.isoformat(),
            in_flight_statuses=[s.value for s in IN_FLIGHT_JOB_STATUSES],
        )

        requeued = 0
        flagged = 0
        for doc in stale:
            job_id = doc.get("id")
            if doc.get("status") == JobStatus.GENERATING.value:
                await self._db.update_campaign_job(
                    job_id,
                    {
                        "status": JobStatus.PENDING.value,
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "updated_at": now.isoformat(),
                    },
                )
                requeued += 1
                continue

            await self._db.update_campaign_job(
                job_id,
                {
                    "status": JobStatus.FAILED.value,
                    "permanent_failure": True,
                    "requires_manual_review": True,
                    "failure_reason": (
                        "Worker stopped while the send was in flight. Not retried "
                        "automatically, because the provider may already have "
                        "accepted the message and a retry could deliver it twice."
                    ),
                    "failure_code": "interrupted_in_flight",
                    "completed_at": now.isoformat(),
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "updated_at": now.isoformat(),
                },
            )
            flagged += 1

        if requeued or flagged:
            logger.warning(
                "Lease recovery: %d job(s) requeued, %d flagged for manual review.",
                requeued,
                flagged,
            )
        return requeued, flagged

    async def cancel_campaign_jobs(self, campaign_id: str) -> int:
        """
        Cancels every job that has not reached a terminal state.

        Jobs already SENT stay SENT: cancelling a campaign stops future sends, it
        cannot unsend what has gone out.
        """
        non_terminal = [
            status.value for status in JobStatus if status not in TERMINAL_JOB_STATUSES
        ]
        now = utc_now()
        cancelled = await self._db.bulk_update_campaign_jobs(
            campaign_id,
            non_terminal,
            {
                "status": JobStatus.CANCELLED.value,
                "lease_owner": None,
                "lease_expires_at": None,
                "next_attempt_at": None,
                "completed_at": now.isoformat(),
                "updated_at": now.isoformat(),
            },
        )
        logger.info("Cancelled %d job(s) for campaign '%s'.", cancelled, campaign_id)
        return cancelled

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, job_id: str) -> Optional[CampaignJob]:
        doc = await self._db.get_campaign_job(job_id)
        return CampaignJob.model_validate(doc) if doc else None

    async def get_by_key(self, idempotency_key: str) -> Optional[CampaignJob]:
        doc = await self._db.get_campaign_job_by_key(idempotency_key)
        return CampaignJob.model_validate(doc) if doc else None

    async def list_jobs(
        self,
        campaign_id: str,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[CampaignJob]:
        docs = await self._db.list_campaign_jobs(campaign_id, status=status, skip=skip, limit=limit)
        return [CampaignJob.model_validate(doc) for doc in docs]

    async def status_counts(self, campaign_id: str) -> Dict[str, int]:
        return await self._db.count_campaign_jobs_by_status(campaign_id)

    async def review_count(self, campaign_id: str) -> int:
        return await self._db.count_campaign_jobs_needing_review(campaign_id)


#: Shared queue instance.
campaign_job_queue = CampaignJobQueue()
