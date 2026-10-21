"""Catalog-only verification without rendering historical application models."""

from django.db.migrations.operations.base import Operation

from legacy_boundary.checkpoint import verify_checkpoint


class VerifyCheckpoint(Operation):
    """Check the physical boundary without changing Django model state."""

    reduces_to_sql = False
    reversible = True

    def state_forwards(self, app_label, state):
        """The checkpoint does not change the active application's models."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        """Read the catalog without materializing historical model registries."""
        verify_checkpoint(None, schema_editor)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        """Reversing verification changes neither storage nor model state."""

    def describe(self):
        """Describe this step in migration plans and collected SQL comments."""
        return 'Verify the observed schema checkpoint and persistent focus defaults'
