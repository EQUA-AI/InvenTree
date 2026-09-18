"""Durable version-bound preparation jobs and a separate embedding spend purpose."""

import uuid

from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone


class Migration(migrations.Migration):
    """Schema only; no schedules, features or provider work are activated."""

    dependencies = [('aichat', '0045_memory_proposals')]
    operations = [
        migrations.CreateModel(
            name='MemoryFactJob',
            fields=[
                (
                    'id',
                    models.UUIDField(
                        primary_key=True, default=uuid.uuid4, editable=False
                    ),
                ),
                (
                    'fact',
                    models.ForeignKey(
                        'aichat.MemoryFact',
                        on_delete=models.CASCADE,
                        related_name='jobs',
                    ),
                ),
                ('fact_version', models.PositiveIntegerField()),
                (
                    'kind',
                    models.CharField(
                        max_length=16,
                        choices=[('shield', 'Shield'), ('embedding', 'Embedding')],
                    ),
                ),
                (
                    'state',
                    models.CharField(
                        max_length=16,
                        default='pending',
                        choices=[
                            ('pending', 'Pending'),
                            ('claimed', 'Claimed'),
                            ('complete', 'Complete'),
                            ('deferred', 'Deferred'),
                            ('failed', 'Failed'),
                            ('skipped', 'Skipped'),
                        ],
                    ),
                ),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('lease_token', models.UUIDField(null=True, blank=True)),
                ('claimed_at', models.DateTimeField(null=True, blank=True)),
                ('next_attempt_at', models.DateTimeField(default=timezone.now)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=['fact', 'fact_version', 'kind'],
                        name='memory_fact_job_version',
                    ),
                    models.CheckConstraint(
                        condition=Q(kind__in=['shield', 'embedding']),
                        name='memory_fact_job_kind',
                    ),
                    models.CheckConstraint(
                        condition=Q(
                            state__in=[
                                'pending',
                                'claimed',
                                'complete',
                                'deferred',
                                'failed',
                                'skipped',
                            ]
                        ),
                        name='memory_fact_job_state',
                    ),
                ],
                'indexes': [
                    models.Index(
                        fields=['state', 'next_attempt_at'],
                        name='memory_fact_job_due_idx',
                    )
                ],
            },
        ),
        migrations.AlterField(
            model_name='aiworkerusageevent',
            name='purpose',
            field=models.CharField(
                max_length=24,
                choices=[
                    ('summarization', 'Summarization'),
                    ('extraction', 'Extraction'),
                    ('embedding', 'Embedding'),
                ],
            ),
        ),
    ]
