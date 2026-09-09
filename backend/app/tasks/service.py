"""
Task service orchestration layer for Resolve AI.
"""

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.database.mongodb import db_manager
from app.tasks.agent import TaskAgent
from app.tasks.models import ExecutionTask, TaskStatus

logger = logging.getLogger(__name__)


class TaskService:
    """
    High-level service interface for ExecutionTasks.
    """

    @classmethod
    async def create_task(
        cls,
        objective: str,
        user_id: str = "default_user",
        attachments: Optional[List[Dict[str, Any]]] = None,
        authorization_scope: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
    ) -> ExecutionTask:
        return await TaskAgent.create_and_run_task(
            objective=objective,
            user_id=user_id,
            attachments=attachments,
            authorization_scope=authorization_scope,
            dry_run=dry_run,
        )

    @classmethod
    async def resume_task(cls, task_id: str, message: Optional[str] = None) -> Optional[ExecutionTask]:
        if message:
            return await TaskAgent.resume_with_message(task_id, message)
        task_dict = await db_manager.get_task(task_id)
        if not task_dict:
            return None
        task = ExecutionTask.model_validate(task_dict)
        if task.status == TaskStatus.PAUSED:
            task.status = TaskStatus.RUNNING if task.actions else TaskStatus.PLANNING
            await db_manager.save_task(task.model_dump(mode="json"))
        return await TaskAgent.step_task(task)

    @classmethod
    async def approve_task(cls, task_id: str) -> Optional[ExecutionTask]:
        return await TaskAgent.approve_and_execute(task_id)

    @classmethod
    async def pause_task(cls, task_id: str) -> Optional[ExecutionTask]:
        task_dict = await db_manager.get_task(task_id)
        if not task_dict:
            return None
        task = ExecutionTask.model_validate(task_dict)
        task.status = TaskStatus.PAUSED
        await db_manager.save_task(task.model_dump(mode="json"))
        return task

    @classmethod
    async def cancel_task(cls, task_id: str) -> Optional[ExecutionTask]:
        task_dict = await db_manager.get_task(task_id)
        if not task_dict:
            return None
        task = ExecutionTask.model_validate(task_dict)
        task.status = TaskStatus.CANCELLED
        from app.tasks.models import ActionStatus
        for action in task.actions:
            if action.status in (ActionStatus.PENDING, ActionStatus.QUEUED, ActionStatus.RUNNING):
                action.status = ActionStatus.CANCELLED
        await db_manager.save_task(task.model_dump(mode="json"))
        return task

    @classmethod
    async def get_task(cls, task_id: str) -> Optional[ExecutionTask]:
        task_dict = await db_manager.get_task(task_id)
        return ExecutionTask.model_validate(task_dict) if task_dict else None

    @classmethod
    async def list_tasks(
        cls,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return await db_manager.list_tasks(user_id=user_id, status=status, skip=skip, limit=limit)

    @classmethod
    async def get_task_jobs(cls, task_id: str) -> List[Dict[str, Any]]:
        return await db_manager.get_task_jobs(task_id)

    @classmethod
    async def get_task_events(cls, task_id: str) -> List[Dict[str, Any]]:
        return await db_manager.get_task_events(task_id)

    @classmethod
    async def stream_task_events(cls, task_id: str) -> AsyncGenerator[str, None]:
        """
        Server-Sent Events generator streaming real-time task updates.
        """
        seen_event_ids = set()
        while True:
            events = await db_manager.get_task_events(task_id, limit=50)
            for e in events:
                eid = e.get("event_id")
                if eid not in seen_event_ids:
                    seen_event_ids.add(eid)
                    yield f"data: {json.dumps(e)}\n\n"

            task_dict = await db_manager.get_task(task_id)
            if task_dict:
                status = task_dict.get("status")
                if status in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value):
                    yield f"event: close\ndata: {json.dumps({'status': status})}\n\n"
                    break

            await asyncio.sleep(1.0)


task_service = TaskService()
