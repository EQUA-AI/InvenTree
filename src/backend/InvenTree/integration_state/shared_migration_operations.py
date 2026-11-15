"""Guard retained shared metadata; never purge or reverse native source history."""

from django.db.migrations.exceptions import IrreversibleError


def guard_shared_state(apps, schema_editor):
    """Refuse retained metadata loss at every admitted historical schema node."""
    connection = schema_editor.connection
    tables = set(connection.introspection.table_names())
    for name in (
        'GenericSourceIdentity',
        'EventConsumer',
        'EventDelivery',
        'ConsumerAspectProgress',
        'ReconciliationScan',
        'ReconciliationIdentity',
        'ReconciliationAttempt',
        'PrivateDeletionRequest',
        'PrivateCopyImpact',
        'PrivateCopyAttempt',
    ):
        try:
            model = apps.get_model('integration_state', name)
        except LookupError:
            # Later allocated models do not exist in earlier historical states.
            continue
        if model._meta.db_table not in tables:
            raise IrreversibleError('Shared state schema is unqualified for backout')
        if model.objects.using(connection.alias).exists():
            raise IrreversibleError(
                'Retained shared metadata requires reviewed recovery'
            )
