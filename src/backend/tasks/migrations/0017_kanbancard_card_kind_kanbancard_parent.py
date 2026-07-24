"""Add child-card composition fields to KanbanCard (S6d)."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add work-order composition (parent + kind)."""

    """Add parent self-FK and card_kind."""

    dependencies = [('tasks', '0016_seed_terminal_column')]

    operations = [
        migrations.AddField(
            model_name='kanbancard',
            name='card_kind',
            field=models.CharField(
                choices=[
                    ('work_order', 'Work Order'),
                    ('subtask', 'Subtask'),
                    ('procurement', 'Procurement'),
                ],
                db_index=True,
                default='work_order',
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='kanbancard',
            name='parent',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='children',
                to='tasks.kanbancard',
            ),
        ),
    ]
