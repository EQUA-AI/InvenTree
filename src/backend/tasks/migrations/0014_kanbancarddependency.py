"""Add the KanbanCardDependency model (S6)."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Introduce work-order dependency edges."""

    """Create the work-order dependency table."""

    dependencies = [('tasks', '0013_workingcalendar')]

    operations = [
        migrations.CreateModel(
            name='KanbanCardDependency',
            fields=[
                (
                    'id',
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'dependency_type',
                    models.CharField(
                        choices=[
                            ('FS', 'Finish-to-Start'),
                            ('SS', 'Start-to-Start'),
                            ('FF', 'Finish-to-Finish'),
                            ('SF', 'Start-to-Finish'),
                        ],
                        default='FS',
                        max_length=2,
                    ),
                ),
                (
                    'lag_minutes',
                    models.IntegerField(
                        default=0,
                        help_text='Working-time slack after the constraint (may be negative)',
                    ),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'from_card',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='dependencies_out',
                        to='tasks.kanbancard',
                    ),
                ),
                (
                    'to_card',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='dependencies_in',
                        to='tasks.kanbancard',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Work Order Dependency',
                'verbose_name_plural': 'Work Order Dependencies',
                'ordering': ['created_at'],
                'unique_together': {('from_card', 'to_card', 'dependency_type')},
            },
        )
    ]
