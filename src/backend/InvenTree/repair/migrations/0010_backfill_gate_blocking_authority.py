"""Backfill blocking authority onto legacy non-blocking safety gates.

An early generation bug wrote safety gates with ``is_blocking=False`` — rows
that render as safety checklist items but never block approval. The creation
paths were fixed (execution-plan S13: a gate inherits its template's blocking
authority, and template-less generated gates default to blocking), but rows
persisted before the fix kept their broken flag. Live census 2026-08-05
(Tranche2TestBattery.md, blocked-item 4): two such gates, a pending LOTO and a
pending isolation on packet RP-000011.

The rule mirrors what creation would do today: a gate is blocking unless its
template explicitly says otherwise. Gates whose template deliberately sets
``is_blocking=False`` are untouched — that is configured authority, not the
bug. Flipping a gate on an open packet can stop that packet's approval until
the gate is confirmed; for LOTO/isolation that is the intended behaviour and
the reason this migration exists.

Reverse is a no-op: un-blocking safety gates in bulk is not a state this
codebase will ever want to restore mechanically.
"""

from django.db import migrations
from django.db.models import Q


def backfill_blocking(apps, schema_editor):
    """Restore template/default blocking authority to legacy rows."""
    RepairPacketGate = apps.get_model('repair', 'RepairPacketGate')

    RepairPacketGate.objects.filter(
        Q(template__isnull=True) | Q(template__is_blocking=True),
        is_blocking=False,
    ).update(is_blocking=True)


class Migration(migrations.Migration):
    """Data-only backfill; no schema change."""

    dependencies = [('repair', '0009_alter_repairpacketevent_event_type')]

    operations = [
        migrations.RunPython(backfill_blocking, migrations.RunPython.noop)
    ]
