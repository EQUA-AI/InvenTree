"""Explicit recording executors for automated tests; never registered at startup."""

from .executors import ApprovalExecutor, DriftReport, EffectResult
from .models import ActionType


class RecordingPurchaseExecutor(ApprovalExecutor):
    """Exercise approval transitions without purchasing or contacting a provider."""

    action_type = ActionType.PURCHASE_ORDER
    implemented = True

    def __init__(self):
        """Keep a per-test, in-memory record of calls."""
        self.calls = []

    def validate(self, payload):
        """No business fixture IDs are required by a transport-only test."""
        return []

    def check_preconditions(self, payload, baseline_context):
        """Tests may replace this hook with drift or failure injection."""
        return DriftReport(False)

    def execute(self, payload, idempotency_key):
        """Record only; this helper has no network or business-write dependencies."""
        self.calls.append((payload, idempotency_key))
        return EffectResult(True, f'recording-{idempotency_key}', {'recording': True})
