"""Deterministically resolve duplicate maintenance rows per work order.

Feature #15 (Closeout Automation) promotes ``AssetMaintenanceRecord.work_order``
to a one-to-one relation so the database guarantees exactly one asset-history
row per completed work order. Legacy data may hold several rows pointing at the
same work order; the deterministic, record-preserving rule is: the newest row
(highest primary key) keeps the link, older rows keep their content but drop
the link. No row is deleted.
"""

from django.db import migrations
from django.db.models import Count


def dedupe_work_order_links(apps, schema_editor):
    """Keep the newest linked row per work order; unlink older duplicates."""
    AssetMaintenanceRecord = apps.get_model('assets', 'AssetMaintenanceRecord')
    duplicated = (
        AssetMaintenanceRecord.objects.filter(work_order__isnull=False)
        .values('work_order')
        .annotate(rows=Count('id'))
        .filter(rows__gt=1)
        .values_list('work_order', flat=True)
    )
    for work_order_id in list(duplicated):
        rows = list(
            AssetMaintenanceRecord.objects.filter(work_order_id=work_order_id)
            .order_by('-id')
            .values_list('id', flat=True)
        )
        stale = rows[1:]
        AssetMaintenanceRecord.objects.filter(id__in=stale).update(work_order=None)


class Migration(migrations.Migration):
    """Record-preserving cleanup before the one-to-one constraint lands."""

    dependencies = [('assets', '0003_assetmachine_customer_idx')]

    operations = [
        migrations.RunPython(dedupe_work_order_links, migrations.RunPython.noop)
    ]
