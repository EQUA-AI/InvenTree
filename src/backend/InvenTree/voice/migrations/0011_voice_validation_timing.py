"""Nullable observation fields; old rows and old writers remain compatible."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Do not invent playback history or modify execution state."""

    dependencies = [('voice', '0010_voicecapturereview')]
    operations = [
        migrations.AddField(
            'voicesession', 'timing_epoch', models.UUIDField(null=True, blank=True)
        ),
        migrations.AddField(
            'voicesession',
            'timing_epoch_started_at',
            models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            'voiceutterance',
            'first_playback_at',
            models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            'voiceutterance',
            'timing_reported_at',
            models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            'voiceutterance', 'timing_metrics', models.JSONField(null=True, blank=True)
        ),
    ]
