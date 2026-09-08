import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from app.automation.models import AutomationJob, AutomationStatus
from app.automation.retry_service import retry_service
from app.database.mongodb import db_manager

logger = logging.getLogger(__name__)

class JobQueue:
    """
    Persistent queue backed by MongoDB with lease-based concurrency and crash resilience.
    """

    async def enqueue(self, job: AutomationJob) -> AutomationJob:
        """Enqueues a single job into persistent storage."""
        await db_manager.save_job(job.model_dump())
        logger.debug(f"Enqueued job '{job.id}' for campaign '{job.campaign_id}'.")
        return job

    async def enqueue_batch(self, jobs: List[AutomationJob]) -> List[AutomationJob]:
        """Enqueues a batch of jobs."""
        for job in jobs:
            await db_manager.save_job(job.model_dump())
        logger.info(f"Enqueued batch of {len(jobs)} jobs.")
        return jobs

    async def dequeue(
        self,
        worker_id: str,
        lease_duration_seconds: int = 30,
        campaign_id: Optional[str] = None
    ) -> Optional[AutomationJob]:
        """
        Atomically leases the next available READY job.
        Survives worker crashes: uncompleted jobs are re-queued after lease expiry.
        """
        job_dict = await db_manager.find_and_lock_next_job(
            worker_id=worker_id,
            lease_duration_seconds=lease_duration_seconds,
            campaign_id=campaign_id
        )
        if not job_dict:
            return None
        return AutomationJob(**job_dict)

    async def ack(self, job_id: str, result: Dict[str, Any]) -> Optional[AutomationJob]:
        """
        Acknowledges successful completion of a job.
        """
        now = datetime.now(timezone.utc).isoformat()
        updates = {
            "status": AutomationStatus.COMPLETED.value,
            "result": result,
            "completed_at": now,
            "lease_owner": None,
            "lease_expires_at": None,
            "updated_at": now
        }
        updated_dict = await db_manager.update_job(job_id, updates)
        if updated_dict:
            logger.info(f"ACK completed for job '{job_id}'.")
            return AutomationJob(**updated_dict)
        return None

    async def nack(self, job_id: str, error_message: str) -> Optional[AutomationJob]:
        """
        Rejects a job on failure, triggering retry evaluation.
        """
        job_dict = await db_manager.get_job(job_id)
        if not job_dict:
            return None

        job = AutomationJob(**job_dict)
        updated_job = retry_service.process_failure(job, error_message)

        now = datetime.now(timezone.utc).isoformat()
        updates = {
            "status": updated_job.status.value,
            "error": updated_job.error,
            "attempt_count": updated_job.attempt_count,
            "completed_at": updated_job.completed_at.isoformat() if updated_job.completed_at else None,
            "lease_owner": None,
            "lease_expires_at": None,
            "updated_at": now
        }
        res = await db_manager.update_job(job_id, updates)
        if res:
            logger.warning(f"NACK processed for job '{job_id}' (Status: {updated_job.status.value}).")
            return AutomationJob(**res)
        return None

    async def recover_stale_jobs(self, lease_timeout_seconds: int = 30) -> int:
        """
        Reclaims jobs orphaned by dead or crashed workers whose lease has expired.
        """
        recovered = await db_manager.recover_stale_jobs(lease_timeout_seconds)
        if recovered > 0:
            logger.warning(f"Recovered {recovered} orphaned/stale jobs from dead workers.")
        return recovered

job_queue = JobQueue()
