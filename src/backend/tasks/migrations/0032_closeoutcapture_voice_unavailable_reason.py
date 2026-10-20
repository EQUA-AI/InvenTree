"""Retain the technician's reason for using a typed story instead of voice."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Additive, reversible capture metadata; no legacy reason is invented."""

    dependencies = [('tasks', '0031_investigation_requests')]
    operations = [
        migrations.AddField(
            model_name='closeoutcapture',
            name='voice_unavailable_reason',
            field=models.CharField(blank=True, default='', max_length=500),
        )
    ]
