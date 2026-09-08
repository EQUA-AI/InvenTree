"""M2 §8.3: the content-free ChatCompactionEvent ledger (additive, dark-safe).

A brand-new table nothing references, so the other environment's revision
keeps running unchanged during the dark window; no existing column moves.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Create ChatCompactionEvent with its thread and outcome indexes."""

    dependencies = [('aichat', '0031_attachment_rag_rebuildable')]

    operations = [
        migrations.CreateModel(
            name='ChatCompactionEvent',
            fields=[
                (
                    'id',
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('task_id', models.CharField(blank=True, default='', max_length=64)),
                (
                    'outcome',
                    models.CharField(
                        choices=[
                            ('started', 'Started'),
                            ('ok', 'Summarized'),
                            ('failed', 'Failed'),
                            ('skipped', 'Skipped'),
                            ('content_filter', 'Content filter'),
                            ('cap_hit', 'Protected cap hit'),
                            ('race_lost', 'Watermark race lost'),
                            ('budget_deferred', 'Budget deferred'),
                        ],
                        default='started',
                        max_length=24,
                    ),
                ),
                ('error_code', models.CharField(blank=True, default='', max_length=64)),
                (
                    'deployment',
                    models.CharField(blank=True, default='', max_length=128),
                ),
                (
                    'reasoning_effort',
                    models.CharField(blank=True, default='', max_length=16),
                ),
                ('input_tokens', models.PositiveIntegerField(default=0)),
                ('output_tokens', models.PositiveIntegerField(default=0)),
                ('latency_ms', models.PositiveIntegerField(default=0)),
                ('from_sequence', models.PositiveBigIntegerField(default=0)),
                ('through_sequence', models.PositiveBigIntegerField(default=0)),
                ('message_count', models.PositiveIntegerField(default=0)),
                ('transcript_chars', models.PositiveIntegerField(default=0)),
                ('truncated', models.BooleanField(default=False)),
                ('cap_hit', models.BooleanField(default=False)),
                ('kept', models.PositiveIntegerField(default=0)),
                ('superseded', models.PositiveIntegerField(default=0)),
                ('dropped', models.PositiveIntegerField(default=0)),
                ('tombstone_hits', models.PositiveIntegerField(default=0)),
                ('redacted_counts', models.JSONField(blank=True, default=dict)),
                ('directives_stripped', models.PositiveIntegerField(default=0)),
                ('entropy_flags', models.PositiveIntegerField(default=0)),
                (
                    'flag_state',
                    models.CharField(
                        blank=True,
                        choices=[
                            ('full', 'Full'),
                            ('shadow', 'Shadow'),
                            ('off', 'Off'),
                        ],
                        default='',
                        max_length=16,
                    ),
                ),
                (
                    'thread',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='compaction_events',
                        to='aichat.chatthread',
                    ),
                ),
            ],
            options={
                'indexes': [
                    models.Index(
                        fields=['thread', '-started_at'],
                        name='aichat_compaction_thread_idx',
                    ),
                    models.Index(
                        fields=['outcome', 'started_at'],
                        name='aichat_compaction_outcome_idx',
                    ),
                ]
            },
        )
    ]
