"""Retain native authority evidence indefinitely across additive backout."""

from django.db.migrations.exceptions import IrreversibleError


def guard_native_authority(apps, schema_editor):
    """Refuse to drop any retained source birth, attestation or review history."""
    from integration_state.shared_migration_operations import guard_shared_state

    # Preflight existing shared retention before an older-node backout starts
    # removing even empty new tables and their applied-history entry.
    guard_shared_state(apps, schema_editor)
    for name in (
        'NativeAuthorityEpoch',
        'NativeSourceBirth',
        'NativeAuthorityRequest',
        'NativeAuthorityDecision',
        'NativeAuthorityWithdrawal',
    ):
        model = apps.get_model('integration_state', name)
        if model.objects.using(schema_editor.connection.alias).exists():
            raise IrreversibleError('Native authority history must be retained')
