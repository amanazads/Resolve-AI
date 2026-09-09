"""
Authentication and User Identity Context for Resolve AI.
"""

from .context import AuthenticatedUser, get_current_user

__all__ = ["AuthenticatedUser", "get_current_user"]
