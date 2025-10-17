"""
Shared policy-related exceptions.
"""

from typing import List, Optional


class PolicyViolationError(Exception):
    """Raised when a policy rejects an operation."""

    def __init__(self, message: str, messages: Optional[List[str]] = None):
        super().__init__(message)
        self.messages = messages or []

