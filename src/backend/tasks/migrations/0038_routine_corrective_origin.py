"""Local O21 linked-corrective origins; no routine outcome backfill."""

from django.db import migrations, models


def mark_recurrences(apps, schema_editor):
    """Existing links were created only by the recurrence command."""
    apps.get_model('tasks', 'WorkOrder').objects.using(
        schema_editor.connection.alias
    ).filter(recurrence_of__isnull=False).update(corrective_origin_kind='recurrence')


class Migration(migrations.Migration):
    """Extend the existing relation; apply only in an approved environment."""

    dependencies = [('tasks', '0035_process_decisions')]
    operations = [
        migrations.AddField(
            'workorder',
            'corrective_origin_kind',
            models.CharField(
                max_length=24,
                blank=True,
                default='',
                choices=[
                    ('recurrence', 'Recurrence'),
                    ('routine_finding', 'Routine finding'),
                ],
            ),
        ),
        migrations.AddField(
            'workorder', 'corrective_finding', models.TextField(blank=True)
        ),
        migrations.AddField(
            'workorder',
            'corrective_reading',
            models.ForeignKey(
                'tasks.CloseoutReading',
                null=True,
                blank=True,
                on_delete=models.PROTECT,
                related_name='+',
            ),
        ),
        migrations.AddField(
            'workorder',
            'corrective_step',
            models.ForeignKey(
                'tasks.WorkOrderStepExecution',
                null=True,
                blank=True,
                on_delete=models.PROTECT,
                related_name='+',
            ),
        ),
        migrations.RunPython(mark_recurrences, migrations.RunPython.noop),
    ]
