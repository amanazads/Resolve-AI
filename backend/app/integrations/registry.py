"""
Provider selection.

`EMAIL_PROVIDER` decides what the application sends through:

  * "mock"  (default) -- MockEmailProvider. Nothing leaves the process. This is
    the right setting for local development, CI and tests.
  * "gmail"           -- GmailProvider, over OAuth 2.0.

The default is deliberately the mock: an unconfigured deployment should send
nothing rather than fall back to some other transport.
"""

import logging
from typing import Dict, Optional

from app.config import settings
from app.integrations.base import EmailProvider
from app.integrations.mock_provider import MockEmailProvider

logger = logging.getLogger(__name__)

_PROVIDERS: Dict[str, EmailProvider] = {}
_OVERRIDE: Optional[EmailProvider] = None


def _build(name: str) -> EmailProvider:
    if name == "gmail":
        # Imported lazily: importing the Gmail service pulls in the database
        # manager, and the mock path should not require any of that.
        from app.integrations.gmail.service import GmailProvider

        return GmailProvider()
    if name != "mock":
        logger.warning(
            "Unknown EMAIL_PROVIDER '%s'. Falling back to the mock provider so that "
            "no mail is sent through an unconfigured transport.",
            name,
        )
    return MockEmailProvider()


def get_email_provider(name: Optional[str] = None) -> EmailProvider:
    """
    Returns the configured provider (cached per name).

    Pass `name` to request a specific provider regardless of configuration.
    """
    if _OVERRIDE is not None and name is None:
        return _OVERRIDE

    resolved = (name or settings.EMAIL_PROVIDER or "mock").strip().lower()
    if resolved not in _PROVIDERS:
        _PROVIDERS[resolved] = _build(resolved)
    return _PROVIDERS[resolved]


def set_email_provider(provider: Optional[EmailProvider]) -> None:
    """
    Forces a provider for the current process. Intended for tests and for local
    experimentation; pass None to restore configuration-driven selection.
    """
    global _OVERRIDE
    _OVERRIDE = provider


def reset_provider_cache() -> None:
    """Drops cached providers so a settings change takes effect."""
    _PROVIDERS.clear()
