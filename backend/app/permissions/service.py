"""
Permission service: grant, check, revoke, and the audit trail.

The check is the interesting part. It answers "may this user do this, right
now?" from the stored grants, and it is designed to be called **once per
campaign** rather than once per recipient -- that is the whole point of storing
the authorization. Each narrowing field on a grant is a reason a *different*
campaign will not match it.

Every decision and every action taken under one is written to the audit log.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.database.mongodb import db_manager
from app.permissions.models import (
    INTEGRATION_BOUND_SCOPES,
    AuditEvent,
    AuditEventType,
    GrantPermissionRequest,
    PermissionCheck,
    PermissionGrant,
    PermissionRequest,
    utc_now,
)

logger = logging.getLogger(__name__)


class PermissionService:
    """Stores and evaluates user authorizations, and records the audit trail."""

    def __init__(self, db=None):
        self._db = db or db_manager

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    async def record(self, event: AuditEvent) -> AuditEvent:
        """
        Appends one event to the audit log, credential-redacted.

        Audit writes never raise into the caller: a failure to log must not turn
        a successful send into an error, though it is logged loudly.
        """
        safe = event.sanitized()
        try:
            await self._db.save_audit_event(safe.model_dump(mode="json"))
        except Exception as exc:  # pragma: no cover - storage failure path
            logger.error("Failed to write audit event %s: %s", safe.event.value, exc)
        return safe

    async def audit(self, event: AuditEventType, **fields: Any) -> AuditEvent:
        """Convenience wrapper: `audit(MESSAGE_SENT, user_id=..., job_id=...)`."""
        return await self.record(AuditEvent(event=event, **fields))

    async def list_audit_events(
        self,
        user_id: Optional[str] = None,
        event: Optional[str] = None,
        campaign_id: Optional[str] = None,
        grant_id: Optional[str] = None,
        integration: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        return await self._db.list_audit_events(
            user_id=user_id,
            event=event,
            campaign_id=campaign_id,
            grant_id=grant_id,
            integration=integration,
            skip=skip,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # Grants
    # ------------------------------------------------------------------

    async def grant(self, request: GrantPermissionRequest) -> PermissionGrant:
        """
        Records an explicit user authorization.

        An integration-bound scope must name its integration: "allow sending"
        without saying from which mailbox is not a permission anyone can audit.
        """
        integration_bound = [s for s in request.scopes if s in INTEGRATION_BOUND_SCOPES]
        if integration_bound and not request.integration:
            raise ValueError(
                f"Scope(s) {[s.value for s in integration_bound]} must name the integration "
                "they apply to (e.g. integration='gmail')."
            )

        grant = PermissionGrant(
            user_id=request.user_id,
            scopes=list(dict.fromkeys(request.scopes)),
            integration=request.integration,
            integration_account_id=request.integration_account_id,
            campaign_id=request.campaign_id,
            dataset_id=request.dataset_id,
            audience=[a.upper() for a in request.audience],
            max_recipients=request.max_recipients,
            granted_by=request.granted_by,
            expires_at=request.expiry(),
            note=request.note,
        )

        await self._db.save_permission_grant(grant.model_dump(mode="json"))
        await self.audit(
            AuditEventType.PERMISSION_GRANTED,
            user_id=grant.user_id,
            actor=grant.granted_by,
            grant_id=grant.grant_id,
            scopes=grant.scopes,
            integration=grant.integration,
            integration_account_id=grant.integration_account_id,
            campaign_id=grant.campaign_id,
            dataset_id=grant.dataset_id,
            outcome="GRANTED",
            detail=grant.describe(),
            metadata={"audience": grant.audience, "max_recipients": grant.max_recipients},
        )
        logger.info("Permission granted: %s (%s)", grant.grant_id, grant.describe())
        return grant

    async def get(self, grant_id: str) -> Optional[PermissionGrant]:
        doc = await self._db.get_permission_grant(grant_id)
        return PermissionGrant.model_validate(doc) if doc else None

    async def list_grants(
        self,
        user_id: Optional[str] = None,
        integration: Optional[str] = None,
        campaign_id: Optional[str] = None,
        include_revoked: bool = False,
        skip: int = 0,
        limit: int = 100,
    ) -> List[PermissionGrant]:
        docs = await self._db.list_permission_grants(
            user_id=user_id,
            integration=integration,
            campaign_id=campaign_id,
            include_revoked=include_revoked,
            skip=skip,
            limit=limit,
        )
        return [PermissionGrant.model_validate(doc) for doc in docs]

    async def revoke(
        self,
        grant_id: str,
        revoked_by: str = "user",
        reason: Optional[str] = None,
    ) -> Optional[PermissionGrant]:
        """
        Revokes a grant. Idempotent, and effective immediately: the next check
        that would have matched this grant no longer does.
        """
        grant = await self.get(grant_id)
        if grant is None:
            return None
        if grant.revoked:
            return grant

        grant.revoked = True
        grant.revoked_at = utc_now()
        grant.revoked_by = revoked_by
        grant.revoke_reason = reason
        await self._db.save_permission_grant(grant.model_dump(mode="json"))

        await self.audit(
            AuditEventType.PERMISSION_REVOKED,
            user_id=grant.user_id,
            actor=revoked_by,
            grant_id=grant.grant_id,
            scopes=grant.scopes,
            integration=grant.integration,
            campaign_id=grant.campaign_id,
            outcome="REVOKED",
            detail=reason or "Permission revoked.",
        )
        logger.info("Permission revoked: %s by %s", grant_id, revoked_by)
        return grant

    async def revoke_all_for_user(self, user_id: str, revoked_by: str = "user") -> int:
        grants = await self.list_grants(user_id=user_id, include_revoked=False, limit=1000)
        for grant in grants:
            await self.revoke(grant.grant_id, revoked_by=revoked_by, reason="Bulk revocation.")
        return len(grants)

    # ------------------------------------------------------------------
    # Checking
    # ------------------------------------------------------------------

    def _match(
        self, grant: PermissionGrant, request: PermissionRequest, now: datetime
    ) -> List[str]:
        """
        Returns the reasons this grant does NOT satisfy the request.

        An empty list means it does. Each entry is phrased so it can be shown to
        the user as "here is what you would need to authorize".
        """
        reasons: List[str] = []

        if grant.revoked:
            reasons.append("The grant has been revoked.")
        if grant.is_expired(now):
            reasons.append(f"The grant expired at {grant.expires_at}.")
        if request.scope not in grant.scopes:
            reasons.append(
                f"The grant does not include the {request.scope.value} scope."
            )

        # Integration. An approval to send from one mailbox is not an approval
        # to send from another.
        if request.scope in INTEGRATION_BOUND_SCOPES or request.integration:
            if grant.integration != request.integration:
                reasons.append(
                    f"The grant is for integration '{grant.integration}', "
                    f"but this action uses '{request.integration}'."
                )
            elif (
                grant.integration_account_id
                and request.integration_account_id
                and grant.integration_account_id != request.integration_account_id
            ):
                reasons.append(
                    f"The grant is for account '{grant.integration_account_id}', "
                    f"not '{request.integration_account_id}'."
                )

        # Campaign. A grant pinned to one campaign does not carry to the next.
        if grant.campaign_id is not None and grant.campaign_id != request.campaign_id:
            reasons.append(
                f"The grant is limited to campaign '{grant.campaign_id}'."
            )

        # Dataset.
        if grant.dataset_id is not None and grant.dataset_id != request.dataset_id:
            reasons.append(f"The grant is limited to dataset '{grant.dataset_id}'.")

        # Audience: the request's audience must be inside the grant's.
        if grant.audience:
            requested = {a.upper() for a in request.audience}
            allowed = set(grant.audience)
            outside = sorted(requested - allowed)
            if not requested:
                reasons.append(
                    f"The grant is limited to audience {sorted(allowed)}, but this "
                    "action targets an unrestricted audience."
                )
            elif outside:
                reasons.append(
                    f"The grant covers audience {sorted(allowed)}, but this action "
                    f"also targets {outside}."
                )

        # Recipient ceiling.
        if grant.max_recipients is not None and request.recipient_count > grant.max_recipients:
            reasons.append(
                f"This action would affect {request.recipient_count} recipients, above "
                f"the grant's ceiling of {grant.max_recipients}."
            )

        return reasons

    async def check(
        self, request: PermissionRequest, record_denial: bool = True
    ) -> PermissionCheck:
        """
        Decides whether `request` is covered by an existing grant.

        Called once per campaign, not once per recipient: a matching grant is
        what lets the agent stop asking. Denials are audited, because a refused
        action is exactly the thing someone will later want to find.
        """
        now = utc_now()
        grants = await self.list_grants(
            user_id=request.user_id, include_revoked=False, limit=1000
        )

        near_misses: List[Dict[str, Any]] = []
        for grant in grants:
            failures = self._match(grant, request, now)
            if not failures:
                await self._mark_used(grant)
                await self.audit(
                    AuditEventType.PERMISSION_USED,
                    user_id=request.user_id,
                    grant_id=grant.grant_id,
                    scopes=[request.scope],
                    integration=request.integration,
                    campaign_id=request.campaign_id,
                    dataset_id=request.dataset_id,
                    outcome="ALLOWED",
                    detail=(
                        f"{request.scope.value} allowed for {request.recipient_count} "
                        f"recipient(s) under grant {grant.grant_id}."
                    ),
                )
                return PermissionCheck(
                    allowed=True,
                    scope=request.scope,
                    grant_id=grant.grant_id,
                    matched_grant=grant,
                    requires_authorization=False,
                    reasons=[f"Covered by grant {grant.grant_id}: {grant.describe()}"],
                )

            if request.scope in grant.scopes:
                near_misses.append({"grant_id": grant.grant_id, "reasons": failures})

        reasons = [
            f"No active grant covers {request.scope.value} for user '{request.user_id}'"
            + (f" via '{request.integration}'" if request.integration else "")
            + "."
        ]
        if near_misses:
            reasons.append(
                "Existing grant(s) with this scope did not match: "
                + "; ".join(
                    f"{miss['grant_id']} ({' '.join(miss['reasons'])})" for miss in near_misses
                )
            )

        check = PermissionCheck(
            allowed=False,
            scope=request.scope,
            requires_authorization=True,
            reasons=reasons,
            near_misses=near_misses,
        )

        if record_denial:
            await self.audit(
                AuditEventType.PERMISSION_DENIED,
                user_id=request.user_id,
                scopes=[request.scope],
                integration=request.integration,
                campaign_id=request.campaign_id,
                dataset_id=request.dataset_id,
                outcome="DENIED",
                detail=" ".join(reasons),
                metadata={"recipient_count": request.recipient_count},
            )
        return check

    async def check_all(
        self, requests: List[PermissionRequest]
    ) -> Dict[str, PermissionCheck]:
        """Checks several scopes at once, e.g. CAMPAIGN_EXECUTE plus EMAIL_SEND."""
        results: Dict[str, PermissionCheck] = {}
        for request in requests:
            results[request.scope.value] = await self.check(request)
        return results

    async def _mark_used(self, grant: PermissionGrant) -> None:
        grant.usage_count += 1
        grant.last_used_at = utc_now()
        await self._db.save_permission_grant(grant.model_dump(mode="json"))


#: Shared service instance.
permission_service = PermissionService()
