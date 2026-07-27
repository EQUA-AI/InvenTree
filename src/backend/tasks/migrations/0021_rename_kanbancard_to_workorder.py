"""Name the work-order model for the thing it holds.

``KanbanCard`` was one row doing two jobs: the maintenance work order, and the
card representing it on the board. Migration 0020 moved it into
``tasks_workorder`` because the table holds work orders; this renames the model
to match, which frees the ``KanbanCard`` name for the board card that follows.

The table itself does not move - ``RenameModel`` is a no-op at the database
level when old and new ``db_table`` agree - so the only physical changes here
are the two satellite tables and the dependency edge's column names.

Renaming a model renames the permissions Django derives from it, and
``users.ruleset`` keys on ``<app_label>_<model_name>``. ``post_migrate``
creates the new ``*_workorder`` permissions; the stale ``*_kanbancard`` rows are
deleted below so no group can be granted a permission that governs nothing.
"""

from django.db import migrations


def drop_stale_permissions(apps, schema_editor):
    """Remove permissions named for the model's old name."""
    Permission = apps.get_model('auth', 'Permission')
    Permission.objects.filter(
        content_type__app_label='tasks',
        codename__regex=r'_(kanbancard|kanbancardpart|kanbancarddependency)$',
    ).delete()


def noop(apps, schema_editor):
    """Reverse is a no-op: post_migrate recreates whatever is missing."""


class Migration(migrations.Migration):
    """Rename the model, its satellites, and the dependency endpoints."""

    dependencies = [('tasks', '0020_alter_kanbancard_table')]

    operations = [
        migrations.RenameModel(old_name='KanbanCard', new_name='WorkOrder'),
        migrations.RenameModel(old_name='KanbanCardPart', new_name='WorkOrderPart'),
        migrations.RenameModel(
            old_name='KanbanCardDependency', new_name='WorkOrderDependency'
        ),
        migrations.AlterModelOptions(
            name='workorder',
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Work Order',
                'verbose_name_plural': 'Work Orders',
            },
        ),
        # The endpoints were named for cards when a work order and a card were
        # the same row. Name them for their role in the edge instead.
        migrations.RenameField(
            model_name='workorderdependency',
            old_name='from_card',
            new_name='predecessor',
        ),
        migrations.RenameField(
            model_name='workorderdependency', old_name='to_card', new_name='successor'
        ),
        migrations.AlterUniqueTogether(
            name='workorderdependency',
            unique_together={('predecessor', 'successor', 'dependency_type')},
        ),
        migrations.RenameField(
            model_name='workorderpart', old_name='card', new_name='work_order'
        ),
        migrations.AlterUniqueTogether(
            name='workorderpart', unique_together={('work_order', 'part')}
        ),
        migrations.RunPython(drop_stale_permissions, noop),
    ]
