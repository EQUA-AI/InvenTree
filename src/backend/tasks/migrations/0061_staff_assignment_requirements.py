"""Add trade requirements without changing retained maintenance structures."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Keep legacy work unclassified and compatible with older workers."""

    dependencies = [
        ('tasks', '0060_jobkitallocation_issued_by'),
        ('legacy_boundary', '0001_observed_schema_checkpoint'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='workorder',
            options={
                'ordering': ['-created_at'],
                'permissions': [
                    ('plan_workorder', 'Can plan work orders'),
                    ('assign_workorder', 'Can assign work orders'),
                    (
                        'override_assignment_trade_mismatch',
                        'Can override assignment trade mismatch',
                    ),
                    ('transition_workorder', 'Can transition work orders'),
                    ('execute_workorder', 'Can execute work orders'),
                    ('complete_workorder', 'Can complete work orders'),
                    ('view_workorder_audit', 'Can view work order audit surfaces'),
                ],
                'verbose_name': 'Work Order',
                'verbose_name_plural': 'Work Orders',
            },
        ),
        migrations.AddField(
            model_name='workorder',
            name='required_trades',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='workorder',
            name='trade_requirements_confirmed',
            field=models.BooleanField(db_default=False, default=False),
        ),
    ]
