"""Preserve deletion policies for tables retired only from migration state.

Historical models stay in an isolated Apps registry. Only live parent models
receive deletion handlers; no retired models or reverse managers are published.
"""

from django.apps import apps
from django.db import connections
from django.db.migrations.loader import MigrationLoader
from django.db.models.deletion import PROTECT, SET_NULL, ProtectedError
from django.db.models.signals import pre_delete


def _deletion_receiver(parent, fields):
    """Build a live-parent guard using the original historical field metadata."""

    def preserve_references(sender, instance, using, **kwargs):
        """Check protection before nulling references in the collector transaction."""
        if sender is not parent:
            return

        connection = connections[using]
        with connection.cursor() as cursor:
            tables = set(connection.introspection.table_names(cursor))

        references = []
        for field in fields:
            if field.model._meta.db_table not in tables:
                continue

            target_value = getattr(instance, field.target_field.attname)
            if target_value is None:
                continue
            references.append((
                field,
                field.model._base_manager.using(using).filter(**{
                    field.attname: target_value
                }),
            ))

        for field, queryset in references:
            if field.remote_field.on_delete is PROTECT and queryset.exists():
                raise ProtectedError(
                    'Cannot delete an object referenced by retained historical data.',
                    (),
                )

        for field, queryset in references:
            if field.remote_field.on_delete is SET_NULL:
                queryset.update(**{field.attname: None})

    return preserve_references


def bind_retired_relations():
    """Bind retained SET_NULL and PROTECT policies without database access.

    Infer relations from the state-only retirement boundary rather than defining
    runtime replacements. Missing tables are skipped only after introspection at
    deletion time; database errors propagate and protected rows are not exposed.
    """
    loader = MigrationLoader(None)
    before = loader.project_state([('assets', '0019_merge_iot_registry_demometrics')])
    after = loader.project_state([('assets', '0020_retire_synthetic_ledger')])
    history = before.apps
    relations = {}

    for model_key in sorted(before.models.keys() - after.models.keys()):
        source = history.get_model(*model_key)
        for field in source._meta.local_fields:
            if not field.many_to_one or field.remote_field.on_delete not in (
                SET_NULL,
                PROTECT,
            ):
                continue

            target = field.remote_field.model
            target_key = (target._meta.app_label, target._meta.model_name)
            if target_key not in after.models:
                continue

            parent = apps.get_model(*target_key)
            relations.setdefault(parent._meta.concrete_model, []).append(field)

    for parent in apps.get_models():
        fields = relations.get(parent._meta.concrete_model)
        if not fields:
            continue
        pre_delete.connect(
            _deletion_receiver(parent, tuple(fields)),
            sender=parent,
            weak=False,
            dispatch_uid=f'{__name__}.{parent._meta.label_lower}',
        )
