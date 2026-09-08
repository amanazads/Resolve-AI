import asyncio
import logging
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from app.automation.models import (
    Campaign,
    AutomationPlan,
    AutomationJob,
    AutomationStatus
)
from app.automation.planner import AutomationPlanner
from app.automation.executor import executor
from app.automation.job_queue import job_queue
from app.database.mongodb import db_manager

logger = logging.getLogger(__name__)

class CampaignService:
    """
    Orchestrates the lifecycle of autonomous automation campaigns.
    Coordinates Planning, Job Queuing, Deterministic Execution, and Persistence.
    Survives worker crashes, restarts, and partial completions.
    """

    def __init__(self):
        self.planner = AutomationPlanner()
        self._active_workers: Dict[str, asyncio.Task] = {}

    async def create_campaign(
        self,
        goal: str,
        automation_type: str = "general",
        input_datasets: Optional[List[Dict[str, Any]]] = None,
        execution_constraints: Optional[Dict[str, Any]] = None,
        personalization_instructions: Optional[str] = None,
        approval_authorization_scope: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None
    ) -> Campaign:
        """
        Creates an AutomationPlan and translates it into deterministic AutomationJobs.
        """
        logger.info(f"Creating campaign for goal: '{goal}'")
        input_datasets = input_datasets or [{}]

        # 1. Generate execution plan via LLM Planner
        plan: AutomationPlan = await self.planner.create_plan(
            goal=goal,
            automation_type=automation_type,
            input_datasets=input_datasets,
            execution_constraints=execution_constraints,
            personalization_instructions=personalization_instructions,
            approval_authorization_scope=approval_authorization_scope,
            persist=True
        )

        campaign_name = name or f"{automation_type.title()} Campaign - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"
        campaign = Campaign(
            name=campaign_name,
            goal=goal,
            automation_type=automation_type,
            plan_id=plan.id,
            status=AutomationStatus.READY,
            total_jobs=0,
            completed_jobs=0,
            failed_jobs=0,
            pending_jobs=0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            metadata={"plan": plan.model_dump()}
        )

        # 2. Decompose plan into deterministic jobs per recipient
        jobs: List[AutomationJob] = []
        max_retries = plan.execution_constraints.get("max_retries", 3)

        for dataset_item in input_datasets:
            recipient_id = dataset_item.get("id") or dataset_item.get("email") or dataset_item.get("phone") or "target_entity"
            
            for task in plan.tasks:
                # Deterministic composite idempotency key: guarantees at-most-once execution per recipient/action
                idempotency_key = f"{campaign.id}:{task.action}:{recipient_id}"
                
                payload = dict(task.parameters)
                payload.update(dataset_item)
                if personalization_instructions:
                    payload["personalization_instructions"] = personalization_instructions

                job = AutomationJob(
                    campaign_id=campaign.id,
                    task_id=task.id,
                    recipient_id=recipient_id,
                    action=task.action,
                    status=AutomationStatus.READY,
                    attempt_count=0,
                    max_retries=max_retries,
                    idempotency_key=idempotency_key,
                    payload=payload,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                jobs.append(job)

        campaign.total_jobs = len(jobs)
        campaign.pending_jobs = len(jobs)

        # 3. Persist Campaign and Jobs in MongoDB
        await db_manager.save_campaign(campaign.model_dump())
        await job_queue.enqueue_batch(jobs)

        logger.info(f"Campaign '{campaign.id}' created with {len(jobs)} jobs in READY status.")
        return campaign

    async def start_campaign(self, campaign_id: str, run_in_background: bool = True) -> Campaign:
        """
        Transitions campaign to RUNNING and begins deterministic job execution.
        """
        campaign_dict = await db_manager.get_campaign(campaign_id)
        if not campaign_dict:
            raise ValueError(f"Campaign '{campaign_id}' not found.")

        campaign = Campaign(**campaign_dict)
        if campaign.status in [AutomationStatus.COMPLETED, AutomationStatus.CANCELLED]:
            logger.warning(f"Cannot start campaign '{campaign_id}' in state '{campaign.status}'.")
            return campaign

        campaign.status = AutomationStatus.RUNNING
        campaign.updated_at = datetime.now(timezone.utc)
        await db_manager.update_campaign(campaign_id, {"status": AutomationStatus.RUNNING.value})

        logger.info(f"Started campaign '{campaign_id}'.")

        if run_in_background:
            task = asyncio.create_task(self._process_campaign_jobs(campaign_id))
            self._active_workers[campaign_id] = task
        else:
            await self._process_campaign_jobs(campaign_id)

        return campaign

    async def _process_campaign_jobs(self, campaign_id: str):
        """Worker loop processing jobs for a specific campaign."""
        worker_id = f"worker_{uuid.uuid4().hex[:6]}"
        logger.info(f"Worker '{worker_id}' spawned for campaign '{campaign_id}'.")

        while True:
            # Check campaign status: respects PAUSED and CANCELLED
            camp_dict = await db_manager.get_campaign(campaign_id)
            if not camp_dict:
                break
            camp_status = camp_dict.get("status")
            if camp_status != AutomationStatus.RUNNING.value:
                logger.info(f"Worker '{worker_id}' stopping: campaign '{campaign_id}' is {camp_status}.")
                break

            # Dequeue next READY job
            job = await job_queue.dequeue(
                worker_id=worker_id,
                lease_duration_seconds=30,
                campaign_id=campaign_id
            )

            if not job:
                # No more READY jobs in queue
                break

            # Execute job deterministically with idempotency protection
            await executor.execute_job(job)

            # Small yield to prevent event-loop starvation
            await asyncio.sleep(0.01)

        # Update final counters and completion state
        await self._sync_campaign_stats(campaign_id)
        self._active_workers.pop(campaign_id, None)

    async def _sync_campaign_stats(self, campaign_id: str):
        """Syncs campaign counters and checks for overall completion."""
        jobs = await db_manager.get_jobs_by_campaign(campaign_id)
        total = len(jobs)
        completed = sum(1 for j in jobs if j.get("status") == AutomationStatus.COMPLETED.value)
        failed = sum(1 for j in jobs if j.get("status") == AutomationStatus.FAILED.value)
        pending = sum(1 for j in jobs if j.get("status") in [AutomationStatus.READY.value, AutomationStatus.RUNNING.value])

        camp_dict = await db_manager.get_campaign(campaign_id)
        current_status = camp_dict.get("status") if camp_dict else AutomationStatus.RUNNING.value

        new_status = current_status
        if current_status in [AutomationStatus.RUNNING.value, AutomationStatus.READY.value]:
            if pending == 0:
                if failed > 0 and completed == 0:
                    new_status = AutomationStatus.FAILED.value
                else:
                    new_status = AutomationStatus.COMPLETED.value

        updates = {
            "total_jobs": total,
            "completed_jobs": completed,
            "failed_jobs": failed,
            "pending_jobs": pending,
            "status": new_status,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        await db_manager.update_campaign(campaign_id, updates)
        logger.info(f"Campaign '{campaign_id}' stats synced: {completed}/{total} completed, {failed} failed. Status: {new_status}")

    async def pause_campaign(self, campaign_id: str) -> Optional[Campaign]:
        """Pauses a running campaign."""
        camp = await db_manager.get_campaign(campaign_id)
        if not camp:
            return None
        await db_manager.update_campaign(campaign_id, {"status": AutomationStatus.PAUSED.value})
        logger.info(f"Campaign '{campaign_id}' PAUSED.")
        updated = await db_manager.get_campaign(campaign_id)
        return Campaign(**updated) if updated else None

    async def resume_campaign(self, campaign_id: str, run_in_background: bool = True) -> Optional[Campaign]:
        """Resumes a paused campaign."""
        camp = await db_manager.get_campaign(campaign_id)
        if not camp:
            return None
        return await self.start_campaign(campaign_id, run_in_background=run_in_background)

    async def cancel_campaign(self, campaign_id: str) -> Optional[Campaign]:
        """Cancels a campaign and all its non-completed jobs."""
        camp = await db_manager.get_campaign(campaign_id)
        if not camp:
            return None

        # Update campaign
        await db_manager.update_campaign(campaign_id, {"status": AutomationStatus.CANCELLED.value})

        # Cancel all pending/ready jobs
        jobs = await db_manager.get_jobs_by_campaign(campaign_id)
        for job in jobs:
            if job.get("status") in [AutomationStatus.READY.value, AutomationStatus.RUNNING.value]:
                await db_manager.update_job(job["id"], {"status": AutomationStatus.CANCELLED.value})

        logger.info(f"Campaign '{campaign_id}' CANCELLED.")
        await self._sync_campaign_stats(campaign_id)
        updated = await db_manager.get_campaign(campaign_id)
        return Campaign(**updated) if updated else None

    async def get_campaign_status(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        """Returns campaign progress, stats, plan details, and jobs."""
        await self._sync_campaign_stats(campaign_id)
        camp = await db_manager.get_campaign(campaign_id)
        if not camp:
            return None
        jobs = await db_manager.get_jobs_by_campaign(campaign_id)
        return {
            "campaign": camp,
            "jobs": jobs
        }

    async def recover_from_restart(self) -> int:
        """
        Called on system/worker restart to recover stale jobs and resume partial campaigns.
        """
        logger.info("Automation CampaignService running crash recovery check...")
        recovered_jobs = await job_queue.recover_stale_jobs(lease_timeout_seconds=0)

        # Find any campaigns that were RUNNING during restart and sync them
        campaigns = await db_manager.list_campaigns()
        for camp in campaigns:
            if camp.get("status") == AutomationStatus.RUNNING.value:
                await self._sync_campaign_stats(camp["id"])

        logger.info(f"Crash recovery completed: {recovered_jobs} jobs restored.")
        return recovered_jobs

campaign_service = CampaignService()
