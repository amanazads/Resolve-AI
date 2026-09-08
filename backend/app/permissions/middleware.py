"""
Permission enforcement.

`PermissionService` answers whether a stored grant covers an action.
This module is what the rest of the application calls, and it adds the piece the
service deliberately does not know about: **a permission grant is not a
substitute for the provider's own authorization.**

A user can grant EMAIL_SEND for Gmail all they like; if the Gmail account is not
connected over OAuth, or its grant at Google has been revoked, the send is still
refused here. The two checks are independent and both must pass:

    permission grant   -- this user authorized *us* to do this
    OAuth connection   -- the *provider* still authorizes us to act for them

Also provides FastAPI dependencies for routes that need a scope.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.permissions.models import (
    INTEGRATION_BOUND_SCOPES,
    AuditEventType,
    PermissionCheck,
    PermissionRequest,
    PermissionScope,
)
from app.permissions.service import PermissionService, permission_service

logger = logging.getLogger(__name__)


class PermissionDenied(Exception):
    """Raised when an action is not covered by any active grant."""

    def __init__(self, check: PermissionCheck):
        super().__init__(" ".join(check.reasons))
        self.check = check

    @property
    def reasons(self) -> List[str]:
        return self.check.reasons


class IntegrationNotAuthorized(Exception):
    """
    Raised when the provider's own authorization is missing.

    Distinct from PermissionDenied on purpose: the fix is different. One needs a
    user to approve a scope, the other needs the mailbox reconnected at Google.
    """

    def __init__(self, integration: str, detail: str):
        super().__init__(detail)
        self.integration = integration
        self.detail = detail


class PermissionEnforcer:
    """
    Checks a scope, and for integration-bound scopes also checks that the
    provider connection is live.
    """

    def __init__(self, service: Optional[PermissionService] = None):
        self.service = service or permission_service

    # -- provider-side authorization --------------------------------------

    async def integration_status(
        self, integration: str, account_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Asks the integration itself whether it is still authorized.

        Imported lazily so the permissions package does not depend on the
        integrations package at import time.
        """
        if integration == "gmail":
            from app.integrations.gmail.service import gmail_service

            status = await gmail_service.get_status(account_id)
            return {
                "connected": status.connected,
                "state": status.state,
                "detail": status.detail,
                "account": status.email_address,
            }

        from app.integrations.registry import get_email_provider

        try:
            provider = get_email_provider(integration)
        except Exception as exc:  # unknown integration
            return {"connected": False, "state": "UNKNOWN", "detail": str(exc)}

        status = await provider.get_connection_status(account_id)
        return {
            "connected": status.connected,
            "state": status.state.value,
            "detail": status.detail,
            "account": status.email_address,
        }

    async def require_integration_authorization(
        self, integration: str, account_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Raises IntegrationNotAuthorized unless the provider connection is live.

        This is the guarantee that no amount of internal permission-granting can
        route around the OAuth flow.
        """
        status = await self.integration_status(integration, account_id)
        if not status.get("connected"):
            raise IntegrationNotAuthorized(
                integration,
                (
                    f"The '{integration}' integration is not authorized "
                    f"({status.get('state')}). {status.get('detail') or ''} "
                    "A permission grant does not replace the provider's own "
                    "authorization; connect the account first."
                ).strip(),
            )
        return status

    # -- combined check ----------------------------------------------------

    async def check(
        self,
        user_id: str,
        scope: PermissionScope,
        integration: Optional[str] = None,
        integration_account_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        dataset_id: Optional[str] = None,
        audience: Optional[List[str]] = None,
        recipient_count: int = 0,
        verify_integration: bool = True,
    ) -> PermissionCheck:
        """Returns the check result without raising."""
        result = await self.service.check(
            PermissionRequest(
                user_id=user_id,
                scope=scope,
                integration=integration,
                integration_account_id=integration_account_id,
                campaign_id=campaign_id,
                dataset_id=dataset_id,
                audience=audience or [],
                recipient_count=recipient_count,
            )
        )

        if (
            result.allowed
            and verify_integration
            and integration
            and scope in INTEGRATION_BOUND_SCOPES
        ):
            try:
                await self.require_integration_authorization(
                    integration, integration_account_id
                )
            except IntegrationNotAuthorized as exc:
                result.allowed = False
                result.requires_authorization = True
                result.reasons.append(exc.detail)
        return result

    async def require(
        self,
        user_id: str,
        scope: PermissionScope,
        **kwargs: Any,
    ) -> PermissionCheck:
        """Same as `check`, but raises PermissionDenied when not allowed."""
        result = await self.check(user_id=user_id, scope=scope, **kwargs)
        if not result.allowed:
            raise PermissionDenied(result)
        return result

    async def require_campaign_send(
        self,
        user_id: str,
        campaign_id: str,
        integration: str,
        dataset_id: Optional[str] = None,
        audience: Optional[List[str]] = None,
        recipient_count: int = 0,
        integration_account_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, PermissionCheck]:
        """
        The check a campaign run makes: once, at the start.

        CAMPAIGN_EXECUTE is always required. EMAIL_SEND is required only when the
        run will actually contact people -- a dry run generates messages and
        sends nothing, so it does not need permission to send.

        This is the whole point of the design: it runs once per campaign, and
        every one of that campaign's recipients then proceeds without asking
        again. A different campaign, dataset, audience or mailbox produces a
        different request, which the stored grant will not match.
        """
        checks: Dict[str, PermissionCheck] = {}

        checks[PermissionScope.CAMPAIGN_EXECUTE.value] = await self.check(
            user_id=user_id,
            scope=PermissionScope.CAMPAIGN_EXECUTE,
            campaign_id=campaign_id,
            dataset_id=dataset_id,
            audience=audience,
            recipient_count=recipient_count,
            verify_integration=False,
        )

        if not dry_run:
            checks[PermissionScope.EMAIL_SEND.value] = await self.check(
                user_id=user_id,
                scope=PermissionScope.EMAIL_SEND,
                integration=integration,
                integration_account_id=integration_account_id,
                campaign_id=campaign_id,
                dataset_id=dataset_id,
                audience=audience,
                recipient_count=recipient_count,
            )

        denied = [name for name, check in checks.items() if not check.allowed]
        if denied:
            combined = PermissionCheck(
                allowed=False,
                requires_authorization=True,
                reasons=[
                    reason
                    for name in denied
                    for reason in checks[name].reasons
                ],
                near_misses=[
                    miss for name in denied for miss in checks[name].near_misses
                ],
            )
            raise PermissionDenied(combined)

        return checks

    # -- audit shortcuts ---------------------------------------------------

    async def audit(self, event: AuditEventType, **fields: Any):
        return await self.service.audit(event, **fields)


#: Shared enforcer.
enforcer = PermissionEnforcer()


# ==========================================================================
# FastAPI helpers
# ==========================================================================


def permission_error(exc: PermissionDenied) -> HTTPException:
    """
    Turns a denial into a 403 that tells the caller what to authorize.

    The body carries the reasons and the near-miss grants, because a permission
    error is only useful if it says what would fix it.
    """
    return HTTPException(
        status_code=403,
        detail={
            "error": "permission_denied",
            "message": "This action is not covered by an active permission grant.",
            "reasons": exc.check.reasons,
            "near_misses": exc.check.near_misses,
            "how_to_fix": (
                "Grant the required scope at POST /api/permissions, scoped to this "
                "campaign, dataset and integration."
            ),
        },
    )


def integration_error(exc: IntegrationNotAuthorized) -> HTTPException:
    return HTTPException(
        status_code=428,  # Precondition Required: connect the account first
        detail={
            "error": "integration_not_authorized",
            "integration": exc.integration,
            "message": exc.detail,
            "how_to_fix": f"Connect the account at POST /api/integrations/{exc.integration}/connect.",
        },
    )


async def require_scope(
    user_id: str,
    scope: PermissionScope,
    **kwargs: Any,
) -> PermissionCheck:
    """
    Route-level guard.

    Usage inside an endpoint:

        await require_scope(user_id, PermissionScope.CONTACT_READ)

    Raises the appropriate HTTPException rather than a bare exception, so a
    route does not have to translate it.
    """
    try:
        return await enforcer.require(user_id=user_id, scope=scope, **kwargs)
    except PermissionDenied as exc:
        raise permission_error(exc) from exc
    except IntegrationNotAuthorized as exc:
        raise integration_error(exc) from exc
