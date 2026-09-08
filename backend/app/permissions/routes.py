"""
Permission and audit API.

    POST   /api/permissions            grant a scoped authorization
    GET    /api/permissions            list a user's grants
    GET    /api/permissions/{id}       one grant
    DELETE /api/permissions/{id}       revoke
    POST   /api/permissions/check      would this action be allowed?
    GET    /api/permissions/audit/log  the audit trail

No response here carries credential material. A grant records that a user
authorized the use of a connected integration; the tokens for that integration
live in the integration's own encrypted store and never appear in this API.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.permissions.middleware import (
    IntegrationNotAuthorized,
    enforcer,
    integration_error,
)
from app.permissions.models import (
    AuditEventView,
    AuditListResponse,
    GrantPermissionRequest,
    PermissionCheck,
    PermissionGrantView,
    PermissionListResponse,
    PermissionScope,
    RevokeResponse,
)
from app.permissions.service import permission_service

logger = logging.getLogger(__name__)

router = APIRouter()


class PermissionCheckRequest(BaseModel):
    """Ask whether an action would be permitted, without performing it."""

    user_id: str
    scope: PermissionScope
    integration: Optional[str] = None
    integration_account_id: Optional[str] = None
    campaign_id: Optional[str] = None
    dataset_id: Optional[str] = None
    audience: List[str] = Field(default_factory=list)
    recipient_count: int = 0
    verify_integration: bool = True


@router.post("/permissions", response_model=PermissionGrantView, status_code=201)
async def grant_permission(request: GrantPermissionRequest):
    """
    Records an explicit user authorization.

    Example body, for "allow Resolve AI to send emails for Campaign X to the
    contacts in Dataset Y using my connected Gmail account":

        {"user_id": "aman", "scopes": ["CAMPAIGN_EXECUTE", "EMAIL_SEND"],
         "integration": "gmail", "campaign_id": "camp_x", "dataset_id": "ds_y",
         "audience": ["INVESTOR"], "max_recipients": 500, "granted_by": "aman"}

    That one record is then enough for every recipient in that campaign. A
    campaign with a different audience, dataset or mailbox will not match it.
    """
    try:
        grant = await permission_service.grant(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PermissionGrantView.from_grant(grant)


@router.get("/permissions", response_model=PermissionListResponse)
async def list_permissions(
    user_id: Optional[str] = Query(None, description="Filter by user."),
    integration: Optional[str] = Query(None),
    campaign_id: Optional[str] = Query(None),
    include_revoked: bool = Query(False, description="Include revoked grants."),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """
    Lists grants, so a user can see exactly what they have authorized -- and,
    with include_revoked, what they have taken back.
    """
    grants = await permission_service.list_grants(
        user_id=user_id,
        integration=integration,
        campaign_id=campaign_id,
        include_revoked=include_revoked,
        skip=skip,
        limit=limit,
    )
    return PermissionListResponse(
        items=[PermissionGrantView.from_grant(g) for g in grants],
        total=len(grants),
        user_id=user_id,
        include_revoked=include_revoked,
    )


@router.get("/permissions/{grant_id}", response_model=PermissionGrantView)
async def get_permission(grant_id: str):
    grant = await permission_service.get(grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail=f"Permission '{grant_id}' not found.")
    return PermissionGrantView.from_grant(grant)


@router.delete("/permissions/{grant_id}", response_model=RevokeResponse)
async def revoke_permission(
    grant_id: str,
    revoked_by: str = Query("user", description="Who is revoking this."),
    reason: Optional[str] = Query(None),
):
    """
    Revokes a grant, effective immediately.

    The record is kept rather than deleted: an audit trail that forgets what was
    once permitted is not an audit trail. Revoked grants are excluded from
    checks and hidden from the default listing.
    """
    grant = await permission_service.revoke(grant_id, revoked_by=revoked_by, reason=reason)
    if grant is None:
        raise HTTPException(status_code=404, detail=f"Permission '{grant_id}' not found.")
    return RevokeResponse(
        revoked=True,
        grant_id=grant_id,
        detail=f"Revoked by '{revoked_by}'." + (f" Reason: {reason}" if reason else ""),
    )


@router.post("/permissions/check", response_model=PermissionCheck)
async def check_permission(request: PermissionCheckRequest):
    """
    Dry-runs a check. Useful for showing a user what they still need to approve
    before starting a campaign, rather than failing them at send time.
    """
    try:
        return await enforcer.check(
            user_id=request.user_id,
            scope=request.scope,
            integration=request.integration,
            integration_account_id=request.integration_account_id,
            campaign_id=request.campaign_id,
            dataset_id=request.dataset_id,
            audience=request.audience,
            recipient_count=request.recipient_count,
            verify_integration=request.verify_integration,
        )
    except IntegrationNotAuthorized as exc:
        raise integration_error(exc) from exc


@router.get("/permissions/audit/log", response_model=AuditListResponse)
async def list_audit_log(
    user_id: Optional[str] = Query(None),
    event: Optional[str] = Query(None, description="e.g. MESSAGE_SENT"),
    campaign_id: Optional[str] = Query(None),
    grant_id: Optional[str] = Query(None),
    integration: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    """
    The audit trail: permissions granted and revoked, campaigns started,
    messages sent and failed, integrations connected and disconnected.
    """
    events = await permission_service.list_audit_events(
        user_id=user_id,
        event=event,
        campaign_id=campaign_id,
        grant_id=grant_id,
        integration=integration,
        skip=skip,
        limit=limit,
    )
    items: List[AuditEventView] = []
    for record in events:
        try:
            items.append(AuditEventView.model_validate(record))
        except Exception as exc:
            logger.debug("Skipping malformed audit record: %s", exc)
    return AuditListResponse(
        items=items,
        total=len(items),
        filters={
            "user_id": user_id,
            "event": event,
            "campaign_id": campaign_id,
            "grant_id": grant_id,
            "integration": integration,
        },
    )
