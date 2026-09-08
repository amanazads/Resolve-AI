"""
Permission scopes, grants and audit events.

The problem this solves: a user who has said "yes, send this campaign" should
not be asked again for every recipient, but that one "yes" must not become a
standing licence to send anything to anyone from any mailbox.

A grant is therefore a *scoped* record -- tied to a user, an integration, and
optionally a campaign, a dataset, an audience and a recipient ceiling. A send is
checked against it once per campaign, not once per contact. A campaign to a
different audience, or through a different mailbox, does not match and needs its
own grant.

Nothing here holds credentials. A grant records that a user authorized the use of
a connected integration; the credentials for that integration stay in the
integration's own encrypted store, and the OAuth connection is still required
independently -- a permission grant is not a substitute for it.
"""

import re
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ==========================================================================
# Scopes
# ==========================================================================


class PermissionScope(str, Enum):
    """What a grant permits."""

    EMAIL_SEND = "EMAIL_SEND"                # send mail through a connected mailbox
    EMAIL_READ = "EMAIL_READ"                # read mail / delivery metadata
    CONTACT_READ = "CONTACT_READ"            # read stored contacts
    LINKEDIN_ACCESS = "LINKEDIN_ACCESS"      # act through a connected LinkedIn account
    CAMPAIGN_EXECUTE = "CAMPAIGN_EXECUTE"    # run a campaign's jobs


#: Scopes that only make sense against a specific connected integration. A grant
#: for one of these must name the integration, and a check must match it: an
#: approval to send from Gmail is not an approval to send from somewhere else.
INTEGRATION_BOUND_SCOPES = frozenset(
    {PermissionScope.EMAIL_SEND, PermissionScope.EMAIL_READ, PermissionScope.LINKEDIN_ACCESS}
)

#: Integrations the system knows how to act through.
KNOWN_INTEGRATIONS = frozenset({"gmail", "mock", "linkedin"})


# ==========================================================================
# Audit
# ==========================================================================


class AuditEventType(str, Enum):
    PERMISSION_GRANTED = "PERMISSION_GRANTED"
    PERMISSION_REVOKED = "PERMISSION_REVOKED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    PERMISSION_USED = "PERMISSION_USED"
    CAMPAIGN_STARTED = "CAMPAIGN_STARTED"
    MESSAGE_SENT = "MESSAGE_SENT"
    MESSAGE_FAILED = "MESSAGE_FAILED"
    INTEGRATION_CONNECTED = "INTEGRATION_CONNECTED"
    INTEGRATION_DISCONNECTED = "INTEGRATION_DISCONNECTED"


#: The audit log is read by people and kept for a long time, so anything that
#: looks like a credential is redacted before it is written.
_CREDENTIAL_PATTERNS = [
    re.compile(r"\bya29\.[A-Za-z0-9._-]+"),
    re.compile(r"\b1//[A-Za-z0-9._-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bmongodb(?:\+srv)?://[^\s]+"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{12,}", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|password)\b"
        r"\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
]

REDACTED = "[redacted]"


def redact(value: Any) -> Any:
    """Recursively replaces credential-shaped substrings with a marker."""
    if isinstance(value, str):
        cleaned = value
        for pattern in _CREDENTIAL_PATTERNS:
            cleaned = pattern.sub(REDACTED, cleaned)
        return cleaned
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            if re.search(
                r"token|secret|password|credential|encryption_key|api_key",
                str(key),
                re.IGNORECASE,
            ):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class AuditEvent(BaseModel):
    """
    One immutable line in the audit log.

    Written for every permission decision and every action taken under one, so a
    campaign can be traced from the approval that allowed it to each individual
    message and its provider outcome.
    """

    audit_id: str = Field(default_factory=lambda: f"aud_{uuid.uuid4().hex[:12]}")
    event: AuditEventType
    at: datetime = Field(default_factory=utc_now)

    user_id: Optional[str] = None
    actor: Optional[str] = Field(
        default=None, description="Who or what caused the event, if not the user."
    )
    grant_id: Optional[str] = None
    scopes: List[PermissionScope] = Field(default_factory=list)
    integration: Optional[str] = None
    integration_account_id: Optional[str] = None

    campaign_id: Optional[str] = None
    dataset_id: Optional[str] = None
    job_id: Optional[str] = None
    contact_id: Optional[str] = None
    recipient: Optional[str] = None
    message_id: Optional[str] = None

    outcome: Optional[str] = None
    detail: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def sanitized(self) -> "AuditEvent":
        """A copy with credential-shaped content removed."""
        copy = self.model_copy(deep=True)
        copy.detail = redact(copy.detail)
        copy.metadata = redact(copy.metadata)
        return copy


# ==========================================================================
# Grants
# ==========================================================================


class PermissionGrant(BaseModel):
    """
    A user's explicit, scoped, revocable authorization.

    The optional narrowing fields are what stop one approval leaking into the
    next campaign:

        campaign_id  -- this campaign only; None means any campaign (a standing
                        grant, reported as broad by the API)
        dataset_id   -- this contact dataset only
        audience     -- these contact types only; a request for others fails
        max_recipients -- a ceiling this grant cannot exceed
    """

    grant_id: str = Field(default_factory=lambda: f"perm_{uuid.uuid4().hex[:12]}")
    user_id: str
    scopes: List[PermissionScope] = Field(default_factory=list)

    integration: Optional[str] = None
    integration_account_id: Optional[str] = None

    campaign_id: Optional[str] = None
    dataset_id: Optional[str] = None
    audience: List[str] = Field(default_factory=list)
    max_recipients: Optional[int] = None

    granted_by: str = Field(description="Identity of the human who approved this.")
    granted_at: datetime = Field(default_factory=utc_now)
    expires_at: Optional[datetime] = None

    revoked: bool = False
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None
    revoke_reason: Optional[str] = None

    usage_count: int = 0
    last_used_at: Optional[datetime] = None

    note: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # -- state -------------------------------------------------------------

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or utc_now()) >= self.expires_at

    def is_active(self, now: Optional[datetime] = None) -> bool:
        return not self.revoked and not self.is_expired(now)

    @property
    def is_standing(self) -> bool:
        """True when the grant is not pinned to a single campaign."""
        return self.campaign_id is None

    def describe(self) -> str:
        """A one-line, human-readable statement of what this permits."""
        parts = [f"{', '.join(s.value for s in self.scopes)}"]
        if self.integration:
            parts.append(f"via {self.integration}")
        parts.append(
            f"for campaign {self.campaign_id}" if self.campaign_id else "for any campaign"
        )
        if self.dataset_id:
            parts.append(f"on dataset {self.dataset_id}")
        if self.audience:
            parts.append(f"targeting {', '.join(self.audience)}")
        if self.max_recipients is not None:
            parts.append(f"up to {self.max_recipients} recipients")
        if self.expires_at:
            parts.append(f"until {self.expires_at.isoformat()}")
        return " ".join(parts)


# ==========================================================================
# Checks
# ==========================================================================


class PermissionRequest(BaseModel):
    """One thing a caller wants to do, to be checked against the stored grants."""

    user_id: str
    scope: PermissionScope
    integration: Optional[str] = None
    integration_account_id: Optional[str] = None
    campaign_id: Optional[str] = None
    dataset_id: Optional[str] = None
    audience: List[str] = Field(default_factory=list)
    recipient_count: int = 0


class PermissionCheck(BaseModel):
    """
    The result of a check.

    `reasons` explains a denial in terms a person can act on, because the point
    of denying is to tell the user what to authorize next -- not merely to stop.
    """

    allowed: bool = False
    scope: Optional[PermissionScope] = None
    grant_id: Optional[str] = None
    matched_grant: Optional[PermissionGrant] = None
    requires_authorization: bool = True
    reasons: List[str] = Field(default_factory=list)
    #: Grants that were the right scope but failed on a narrowing field, with
    #: why. This is what turns "denied" into "denied, and here is what to change".
    near_misses: List[Dict[str, Any]] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=utc_now)


# ==========================================================================
# API models
# ==========================================================================


class GrantPermissionRequest(BaseModel):
    """
    Body for creating a grant.

    This models an explicit human approval such as: "Allow Resolve AI to send
    emails for Campaign X to the contacts in Dataset Y using my connected Gmail
    account."
    """

    user_id: str
    scopes: List[PermissionScope] = Field(min_length=1)
    integration: Optional[str] = None
    integration_account_id: Optional[str] = None
    campaign_id: Optional[str] = None
    dataset_id: Optional[str] = None
    audience: List[str] = Field(default_factory=list)
    max_recipients: Optional[int] = Field(default=None, ge=1)
    granted_by: str = Field(description="The approving human's identity.")
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=365)
    note: str = ""

    def expiry(self) -> Optional[datetime]:
        if self.expires_in_days is None:
            return None
        return utc_now() + timedelta(days=self.expires_in_days)


class PermissionGrantView(BaseModel):
    """A grant as returned by the API. Carries no credential material."""

    grant_id: str
    user_id: str
    scopes: List[PermissionScope]
    integration: Optional[str] = None
    integration_account_id: Optional[str] = None
    campaign_id: Optional[str] = None
    dataset_id: Optional[str] = None
    audience: List[str] = Field(default_factory=list)
    max_recipients: Optional[int] = None
    granted_by: str
    granted_at: datetime
    expires_at: Optional[datetime] = None
    revoked: bool = False
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None
    revoke_reason: Optional[str] = None
    usage_count: int = 0
    last_used_at: Optional[datetime] = None
    active: bool = True
    expired: bool = False
    standing: bool = False
    summary: str = ""
    note: str = ""

    @classmethod
    def from_grant(cls, grant: PermissionGrant) -> "PermissionGrantView":
        return cls(
            **grant.model_dump(exclude={"metadata"}),
            active=grant.is_active(),
            expired=grant.is_expired(),
            standing=grant.is_standing,
            summary=grant.describe(),
        )


class PermissionListResponse(BaseModel):
    items: List[PermissionGrantView]
    total: int
    user_id: Optional[str] = None
    include_revoked: bool = False


class RevokeResponse(BaseModel):
    revoked: bool
    grant_id: str
    detail: str = ""


class AuditEventView(BaseModel):
    audit_id: str
    event: AuditEventType
    at: datetime
    user_id: Optional[str] = None
    actor: Optional[str] = None
    grant_id: Optional[str] = None
    scopes: List[PermissionScope] = Field(default_factory=list)
    integration: Optional[str] = None
    campaign_id: Optional[str] = None
    dataset_id: Optional[str] = None
    job_id: Optional[str] = None
    contact_id: Optional[str] = None
    recipient: Optional[str] = None
    message_id: Optional[str] = None
    outcome: Optional[str] = None
    detail: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AuditListResponse(BaseModel):
    items: List[AuditEventView]
    total: int
    filters: Dict[str, Any] = Field(default_factory=dict)
