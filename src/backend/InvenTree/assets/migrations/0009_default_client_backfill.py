"""Backfill every machine without a client into the default internal tenant.

Runs before the customer column is removed (0010): a machine that was only
reachable through its customer must already carry a client when that column
disappears, or it would silently become unreachable.
"""

from django.db import migrations


def _assign_default_client(apps, schema_editor):
    """Create the internal tenant and adopt every clientless machine into it."""
    Client = apps.get_model('assets', 'Client')
    AssetMachine = apps.get_model('assets', 'AssetMachine')

    internal, _created = Client.objects.get_or_create(
        code='internal', defaults={'name': 'Internal'}
    )
    AssetMachine.objects.filter(client__isnull=True).update(client=internal)


def _unassign_default_client(apps, schema_editor):
    """Reverse: detach machines from the internal tenant and remove it.

    Only machines pointing at the migration-created tenant are touched, so a
    client assigned by hand survives a rollback.
    """
    Client = apps.get_model('assets', 'Client')
    AssetMachine = apps.get_model('assets', 'AssetMachine')

    internal = Client.objects.filter(code='internal').first()
    if internal is None:
        return
    AssetMachine.objects.filter(client=internal).update(client=None)
    internal.delete()


class Migration(migrations.Migration):
    dependencies = [
        ('assets', '0008_machineanomaly_repair_packet'),
    ]

    operations = [
        migrations.RunPython(_assign_default_client, _unassign_default_client),
    ]
