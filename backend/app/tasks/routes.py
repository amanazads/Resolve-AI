"""
FastAPI route endpoints for ExecutionTasks.
"""

import base64
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.tasks.models import ExecutionTask
from app.tasks.service import task_service

logger = logging.getLogger(__name__)

router = APIRouter()


class TaskCreateRequest(BaseModel):
    objective: str = Field(..., description="Natural language objective for the agent")
    user_id: str = Field(default="default_user")
    attachments: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="List of attachment objects: [{'filename': ..., 'content': base64 or raw, 'metadata': ...}]",
    )
    authorization_scope: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Authorization scopes (e.g. {'allow_send': True})",
    )
    dry_run: bool = Field(default=False, description="If true, generates previews without external actions")


class TaskMessageRequest(BaseModel):
    message: str = Field(..., description="User's response to the agent's clarification question")


@router.post("/tasks", response_model=ExecutionTask)
async def create_task_endpoint(request: TaskCreateRequest):
    """
    Creates a new autonomous execution task from natural language.
    """
    if not request.objective.strip():
        raise HTTPException(status_code=400, detail="Objective cannot be empty.")

    # Decode base64 attachment contents if needed
    clean_attachments = []
    if request.attachments:
        for att in request.attachments:
            content = att.get("content")
            if isinstance(content, str) and att.get("is_base64"):
                try:
                    content = base64.b64decode(content)
                except Exception:
                    content = content.encode("utf-8")
            clean_attachments.append(
                {
                    "filename": att.get("filename", "attachment"),
                    "content": content,
                    "metadata": att.get("metadata", {}),
                }
            )

    try:
        task = await task_service.create_task(
            objective=request.objective,
            user_id=request.user_id,
            attachments=clean_attachments,
            authorization_scope=request.authorization_scope,
            dry_run=request.dry_run,
        )
        return task
    except Exception as e:
        logger.exception("Error creating task: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to create task: {str(e)}")


@router.get("/tasks")
async def list_tasks_endpoint(
    user_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """
    Lists execution tasks.
    """
    tasks = await task_service.list_tasks(user_id=user_id, status=status, skip=skip, limit=limit)
    return {"tasks": tasks}


@router.get("/tasks/{task_id}", response_model=ExecutionTask)
async def get_task_endpoint(task_id: str):
    """
    Retrieves task status, plan, actions, and results.
    """
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return task


@router.post("/tasks/{task_id}/message", response_model=ExecutionTask)
async def send_task_message_endpoint(task_id: str, request: TaskMessageRequest):
    """
    Answers a clarification question asked by the agent and resumes the existing task.
    """
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    task = await task_service.resume_task(task_id=task_id, message=request.message)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return task


@router.post("/tasks/{task_id}/approve", response_model=ExecutionTask)
async def approve_task_endpoint(task_id: str):
    """
    Explicitly approves execution of prepared actions (e.g. sending emails).
    """
    task = await task_service.approve_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return task


@router.post("/tasks/{task_id}/resume", response_model=ExecutionTask)
async def resume_task_endpoint(task_id: str):
    """
    Resumes execution of an existing task.
    """
    task = await task_service.resume_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return task


@router.post("/tasks/{task_id}/pause", response_model=ExecutionTask)
async def pause_task_endpoint(task_id: str):
    """
    Pauses an in-flight task.
    """
    task = await task_service.pause_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return task


@router.post("/tasks/{task_id}/cancel", response_model=ExecutionTask)
async def cancel_task_endpoint(task_id: str):
    """
    Cancels a task.
    """
    task = await task_service.cancel_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return task


@router.get("/tasks/{task_id}/jobs")
async def get_task_jobs_endpoint(task_id: str):
    """
    Lists individual action jobs underneath a task.
    """
    jobs = await task_service.get_task_jobs(task_id)
    return {"task_id": task_id, "jobs": jobs}


@router.get("/tasks/{task_id}/events")
async def get_task_events_stream_endpoint(task_id: str):
    """
    Server-Sent Events stream of real-time task lifecycle events.
    """
    return StreamingResponse(
        task_service.stream_task_events(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/tasks/attachments")
async def upload_task_attachment_endpoint(file: UploadFile = File(...)):
    """
    Uploads a file attachment for use in tasks.
    Returns base64 encoded content and extracted preview.
    """
    try:
        content = await file.read()
        filename = file.filename or "attachment"
        b64_content = base64.b64encode(content).decode("utf-8")
        return {
            "filename": filename,
            "size_bytes": len(content),
            "content": b64_content,
            "is_base64": True,
        }
    except Exception as e:
        logger.error(f"Error reading attachment upload: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")
