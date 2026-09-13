"""Persist exact answer chunks separately from executable decisions."""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add output-only presentation persistence."""

    dependencies = [('voice', '0006_voicesession_preferences')]
    operations = [
        migrations.CreateModel(
            name='VoicePresentation',
            fields=[
                (
                    'id',
                    models.UUIDField(
                        primary_key=True,
                        default=uuid.uuid4,
                        editable=False,
                        serialize=False,
                    ),
                ),
                ('turn_id', models.CharField(max_length=64)),
                ('source_hash', models.CharField(max_length=64)),
                ('utterance_ids', models.JSONField(default=list)),
                ('position', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'session',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='presentations',
                        to='voice.voicesession',
                    ),
                ),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=['session', 'turn_id'], name='voice_presentation_turn'
                    )
                ]
            },
        )
    ]
