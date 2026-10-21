"""Replace the complete recorded batch with an observed structural checkpoint.

The original operation sequence was lost. This migration preserves its observed
boundary without claiming to recover historical data operations or merge parents.
Applied databases adopt the checkpoint after strict catalog validation; fresh
PostgreSQL databases create the same compatibility structures, with no seed data
or authority grants. Retained storage is deliberately outside runtime model state.
"""

from django.db import migrations

from legacy_boundary.checkpoint import LEGACY_MIGRATIONS, install_checkpoint


class Migration(migrations.Migration):
    """Adopt only a complete, structurally verified legacy batch."""

    initial = True
    replaces = list(LEGACY_MIGRATIONS)
    dependencies = [
        ('aichat', '0053_controlled_projection_audit'),
        ('assets', '0022_var_unit'),
        ('repair', '0010_backfill_gate_blocking_authority'),
        ('tasks', '0030_workorder_tasks_wo_actual_completed_and_more'),
        ('users', '0024_notification_publish_permission'),
    ]
    # Irreversible: deleting the reconstructed compatibility storage would lose
    # historical records. Application rollback keeps this additive storage.
    operations = [migrations.RunPython(install_checkpoint)]
