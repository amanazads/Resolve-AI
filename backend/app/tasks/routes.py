"""
FastAPI route endpoints for ExecutionTasks.
"""

import base64
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.context import AuthenticatedUser, get_current_user
from app.config import settings
from app.database.mongodb import db_manager
from app.tasks.attachments import AttachmentProcessor
from app.tasks.models import ExecutionTask
from app.tasks.service import task_service

logger = logging.getLogger(__name__)

router = APIRouter()


class TaskCreateRequest(BaseModel):
    objective: str = Field(..., description="Natural language objective for the agent")
    user_id: Optional[str] = Field(default=None, description="Owner user identifier")

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


def check_user_access(task: ExecutionTask, requesting_user_id: Optional[str]) -> None:
    if requesting_user_id and task.user_id and task.user_id != requesting_user_id:
        # Default local identities are equivalent for single-user dev convenience
        if not (task.user_id in ("default_user", "local_user") and requesting_user_id in ("default_user", "local_user")):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: You do not have access to task '{task.task_id}'.",
            )


@router.post("/tasks", response_model=ExecutionTask)
async def create_task_endpoint(
    request: TaskCreateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Creates a new autonomous execution task from natural language.
    """
    if not request.objective.strip():
        raise HTTPException(status_code=400, detail="Objective cannot be empty.")

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

    owner_id = request.user_id or current_user.user_id
    try:
        task = await task_service.create_task(
            objective=request.objective,
            user_id=owner_id,
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
    Lists execution tasks with optional user and status filters.
    """
    tasks = await task_service.list_tasks(user_id=user_id, status=status, skip=skip, limit=limit)
    return {"tasks": tasks}


@router.get("/tasks/{task_id}", response_model=ExecutionTask)
async def get_task_endpoint(
    task_id: str,
    user_id: Optional[str] = Query(None),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Retrieves task status, plan, actions, and results with cross-user isolation.
    """
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    effective_user = user_id or current_user.user_id
    check_user_access(task, effective_user)
    return task



@router.post("/tasks/{task_id}/message", response_model=ExecutionTask)
async def send_task_message_endpoint(
    task_id: str, request: TaskMessageRequest, user_id: Optional[str] = Query(None)
):
    """
    Answers a clarification question asked by the agent and resumes the existing task.
    """
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    check_user_access(task, user_id)

    resumed = await task_service.resume_task(task_id=task_id, message=request.message)
    return resumed


@router.post("/tasks/{task_id}/clarify", response_model=ExecutionTask)
async def clarify_task_endpoint(
    task_id: str, request: TaskMessageRequest, user_id: Optional[str] = Query(None)
):
    """
    Alias to /message for multi-turn clarification resolution.
    """
    return await send_task_message_endpoint(task_id, request, user_id)


@router.post("/tasks/{task_id}/approve", response_model=ExecutionTask)
async def approve_task_endpoint(task_id: str, user_id: Optional[str] = Query(None)):
    """
    Explicitly approves execution of prepared actions (e.g. sending emails).
    """
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    check_user_access(task, user_id)

    approved = await task_service.approve_task(task_id)
    return approved


@router.post("/tasks/{task_id}/authorize", response_model=ExecutionTask)
async def authorize_task_endpoint(task_id: str, user_id: Optional[str] = Query(None)):
    """
    Alias to /approve for granting scoped authorization.
    """
    return await approve_task_endpoint(task_id, user_id)


@router.post("/tasks/{task_id}/resume", response_model=ExecutionTask)
async def resume_task_endpoint(task_id: str, user_id: Optional[str] = Query(None)):
    """
    Resumes execution of an existing paused task.
    """
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    check_user_access(task, user_id)

    resumed = await task_service.resume_task(task_id)
    return resumed


@router.post("/tasks/{task_id}/resume-execution", response_model=ExecutionTask)
async def resume_execution_task_endpoint(task_id: str, user_id: Optional[str] = Query(None)):
    """
    Alias to /resume.
    """
    return await resume_task_endpoint(task_id, user_id)


@router.post("/tasks/{task_id}/pause", response_model=ExecutionTask)
async def pause_task_endpoint(task_id: str, user_id: Optional[str] = Query(None)):
    """
    Pauses an in-flight task.
    """
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    check_user_access(task, user_id)

    paused = await task_service.pause_task(task_id)
    return paused


@router.post("/tasks/{task_id}/cancel", response_model=ExecutionTask)
async def cancel_task_endpoint(task_id: str, user_id: Optional[str] = Query(None)):
    """
    Cancels a task.
    """
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    check_user_access(task, user_id)

    cancelled = await task_service.cancel_task(task_id)
    return cancelled


@router.get("/tasks/{task_id}/actions")
async def get_task_actions_endpoint(task_id: str, user_id: Optional[str] = Query(None)):
    """
    Returns the list of concrete actions for the task.
    """
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    check_user_access(task, user_id)
    return {"task_id": task_id, "actions": task.actions}


@router.get("/tasks/{task_id}/activity")
async def get_task_activity_endpoint(task_id: str, user_id: Optional[str] = Query(None)):
    """
    Returns audit activity events for the task.
    """
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    check_user_access(task, user_id)
    events = await task_service.get_task_events(task_id)
    return {"task_id": task_id, "events": events}


@router.get("/tasks/{task_id}/jobs")
async def get_task_jobs_endpoint(task_id: str, user_id: Optional[str] = Query(None)):
    """
    Lists individual action jobs underneath a task.
    """
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    check_user_access(task, user_id)

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


@router.get("/tasks/{task_id}/files")
async def get_task_files_endpoint(task_id: str, user_id: Optional[str] = Query(None)):
    """
    Lists all artifacts attached to the task.
    """
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    check_user_access(task, user_id)
    return {"task_id": task_id, "files": task.artifacts}


@router.post("/tasks/{task_id}/files")
async def attach_task_file_endpoint(
    task_id: str, file: UploadFile = File(...), user_id: Optional[str] = Query(None)
):
    """
    Attaches a file directly to an existing task.
    """
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    check_user_access(task, user_id)

    content = await file.read()
    filename = file.filename or "attachment"

    if len(content) > settings.MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of {settings.MAX_FILE_SIZE_BYTES // (1024*1024)}MB.",
        )

    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext and ext not in settings.ALLOWED_FILE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File extension '{ext}' is not permitted.",
        )

    parsed_artifact = AttachmentProcessor.process_file(content=content, filename=filename)
    task.attachments.append(parsed_artifact)
    task.artifacts.append(parsed_artifact)
    await db_manager.save_task(task.model_dump(mode="json"))

    return {"success": True, "artifact": parsed_artifact}


@router.delete("/tasks/{task_id}/files/{file_id}")
async def delete_task_file_endpoint(
    task_id: str, file_id: str, user_id: Optional[str] = Query(None)
):
    """
    Removes an artifact from a task.
    """
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    check_user_access(task, user_id)

    task.attachments = [a for a in task.attachments if a.artifact_id != file_id and a.attachment_id != file_id]
    task.artifacts = [a for a in task.artifacts if a.artifact_id != file_id and a.attachment_id != file_id]
    await db_manager.save_task(task.model_dump(mode="json"))

    return {"success": True, "message": f"Artifact '{file_id}' removed."}


@router.post("/tasks/attachments")
async def upload_task_attachment_endpoint(file: UploadFile = File(...)):
    """
    Uploads a file attachment for use in tasks with strict size and extension security.
    """
    filename = file.filename or "attachment"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext and ext not in settings.ALLOWED_FILE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File extension '{ext}' is not permitted for security reasons.",
        )

    try:
        content = await file.read()
        if len(content) > settings.MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds maximum allowed size of {settings.MAX_FILE_SIZE_BYTES // (1024*1024)}MB.",
            )

        b64_content = base64.b64encode(content).decode("utf-8")
        return {
            "filename": filename,
            "size_bytes": len(content),
            "content": b64_content,
            "is_base64": True,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading attachment upload: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")
