"""Versioned additive placement-capture operations and retained-state guards."""

from django.db.migrations.exceptions import IrreversibleError

from integration_state.capture import install_capture, uninstall_capture


def guard_placement_state(apps, schema_editor):
    """Refuse destructive backout of any retained integration-owned metadata."""
    alias = schema_editor.connection.alias
    for name in (
        'SourceNamespace',
        'SourceState',
        'SourceChange',
        'ProjectionEnrollment',
        'PlacementAcknowledgement',
    ):
        if apps.get_model('integration_state', name).objects.using(alias).exists():
            raise IrreversibleError(
                'Retained integration state needs a reviewed recovery disposition; '
                'native records and integration metadata have not been removed'
            )


def apply_placement_capture(apps, schema_editor):
    """Install the fixed version-one capture on the selected migration database."""
    install_capture(schema_editor.connection)


def reverse_placement_capture(apps, schema_editor):
    """Permit an empty installation's reversal, never an implicit state reset."""
    guard_placement_state(apps, schema_editor)
    uninstall_capture(schema_editor.connection)
