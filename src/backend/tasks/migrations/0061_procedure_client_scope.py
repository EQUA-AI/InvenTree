"""Explicit plant-client procedure ownership; legacy scopes are unchanged."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add optional client ownership without inferring ownership of old procedures."""

    dependencies = [
        ('tasks', '0060_jobkitallocation_issued_by'),
        ('assets', '0023_registry_recorded_facts'),
    ]

    operations = [
        migrations.AddField(
            model_name='procedure', name='client',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='assets.client'),
        ),
        migrations.AddConstraint(
            model_name='procedure',
            constraint=models.UniqueConstraint(fields=('client', 'code'), condition=models.Q(client__isnull=False), name='tasks_procedure_client_code_uniq'),
        ),
        migrations.AddConstraint(
            model_name='procedure',
            constraint=models.CheckConstraint(condition=models.Q(client__isnull=True) | models.Q(customer__isnull=True), name='tasks_procedure_unmixed_scope'),
        ),
    ]
