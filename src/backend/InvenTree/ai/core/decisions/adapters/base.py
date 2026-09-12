"""Explicit six-part action contract, shared by voice adapters."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionSpec:
    """Every executable action names governance, review, and recovery controls."""

    action: str
    target_type: str
    command_and_authorization: str
    preview_fields: tuple[str, ...]
    confirmation: str
    version_idempotency_receipt: str
    compensation_and_failure: str
    available: bool = False
    unavailable_reason: str = "A governed voice adapter is not available for this action."
