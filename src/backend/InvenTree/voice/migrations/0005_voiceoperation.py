"""Durable reconciliation ledger for governed voice decisions."""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Record submission before invoking an executor."""

    dependencies = [('voice', '0004_voiceutterance_prompt_type')]
    operations = [
        migrations.CreateModel(
            name='VoiceOperation',
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
                ('decision_id', models.CharField(max_length=64, unique=True)),
                ('source_id', models.CharField(max_length=255)),
                ('action', models.CharField(max_length=100)),
                ('state', models.CharField(max_length=32, default='executing')),
                ('target_label', models.CharField(max_length=255)),
                ('receipt_ref', models.CharField(max_length=255, blank=True)),
                ('receipt', models.JSONField(default=dict, blank=True)),
                ('detail', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'session',
                    models.ForeignKey(
                        to='voice.voicesession',
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='operations',
                    ),
                ),
            ],
        )
    ]
