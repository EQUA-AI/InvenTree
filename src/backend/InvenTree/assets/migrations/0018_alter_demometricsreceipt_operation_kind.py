"""Alter the receipt operation-kind choices to include apply_session."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the apply-session execution receipt operation to the choices."""

    dependencies = [
        ('assets', '0017_demometricscoverageinterval_demometricssession_and_more')
    ]

    operations = [
        migrations.AlterField(
            model_name='demometricsreceipt',
            name='operation_kind',
            field=models.CharField(
                choices=[
                    ('apply_session', 'Apply session'),
                    ('apply_source', 'Create source'),
                    ('apply_binding', 'Create binding'),
                    ('apply_work_order', 'Create work order'),
                    ('apply_control', 'Import synthetic control'),
                    ('apply_observation', 'Ingest observation'),
                    ('replay_observation', 'Replay observation'),
                    ('stop', 'Stop session'),
                    ('cleanup', 'Cleanup session'),
                ],
                max_length=32,
            ),
        )
    ]
