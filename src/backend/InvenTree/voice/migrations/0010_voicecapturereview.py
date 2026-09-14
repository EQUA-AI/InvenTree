"""Additive page evidence; previous app revisions do not depend on this table."""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Record page bindings without modifying existing session or capture rows."""

    dependencies = [('voice', '0009_transcript_turn_database_default')]

    operations = [
        migrations.CreateModel(
            name='VoiceCaptureReview',
            fields=[
                (
                    'id',
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ('scope_hash', models.CharField(max_length=64)),
                ('target_version', models.PositiveBigIntegerField()),
                ('policy_version', models.CharField(max_length=64)),
                ('page_hashes', models.JSONField(default=list)),
                ('utterance_ids', models.JSONField(default=list)),
                ('invalidated', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'revision',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='voice_reviews',
                        to='voice.voicetranscriptrevision',
                    ),
                ),
                (
                    'session',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='capture_reviews',
                        to='voice.voicesession',
                    ),
                ),
            ],
        )
    ]
