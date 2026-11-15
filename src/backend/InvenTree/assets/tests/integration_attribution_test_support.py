"""Test-only simulation of future historical-attribution evidence.

This helper deliberately admits synthetic rows through attribution filters. It
is not proof of attribution, is never imported by application readers, and must
not be used in tests that claim to verify the actual default-deny policy.
"""

from functools import wraps
from unittest.mock import patch

from assets import integration_attribution


def assume_synthetic_attribution(test_class):
    """Simulate admission solely to exercise downstream prototype controls."""
    original_setup = test_class.setUp

    @wraps(original_setup)
    def set_up_with_simulated_attribution(self):
        for name in (
            'attributed_work_orders',
            'attributed_maintenance_records',
            'attributed_machine_attachments',
        ):
            self.enterContext(
                patch.object(
                    integration_attribution, name, side_effect=lambda rows: rows
                )
            )
        original_setup(self)

    test_class.setUp = set_up_with_simulated_attribution
    return test_class
