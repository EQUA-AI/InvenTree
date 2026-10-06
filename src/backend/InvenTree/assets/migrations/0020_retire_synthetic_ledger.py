"""Retire synthetic ledger model state without deleting historical data.

The tables introduced by 0017 remain inert compatibility storage, including
all existing rows and foreign keys. Applied migrations are intentionally
unchanged. Removing these models from state matches the non-demo runtime;
forward and reverse migration of this step execute no schema or data SQL.
"""

from django.db import migrations


class Migration(migrations.Migration):
    """Keep existing databases compatible while removing the synthetic runtime."""

    dependencies = [('assets', '0019_merge_iot_registry_demometrics')]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name='DemoMetricsDowntimeInterval'),
                migrations.DeleteModel(name='DemoMetricsCoverageInterval'),
                migrations.DeleteModel(name='DemoMetricsMachine'),
                migrations.DeleteModel(name='DemoMetricsObject'),
                migrations.DeleteModel(name='DemoMetricsReceipt'),
                migrations.DeleteModel(name='DemoMetricsSession'),
            ],
        )
    ]
