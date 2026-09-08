"""M2 §8.4: the content-free AIWorkerUsageEvent spend ledger (additive, dark-safe).

A brand-new table nothing references, so the other environment's revision
keeps running unchanged during the dark window; no existing column moves.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Create AIWorkerUsageEvent with its deployment and purpose indexes."""

    dependencies = [('aichat', '0032_chatcompactionevent')]

    operations = [
        migrations.CreateModel(
            name='AIWorkerUsageEvent',
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
                (
                    'purpose',
                    models.CharField(
                        choices=[
                            ('summarization', 'Summarization'),
                            ('extraction', 'Extraction'),
                        ],
                        max_length=24,
                    ),
                ),
                ('task', models.CharField(blank=True, default='', max_length=64)),
                (
                    'deployment',
                    models.CharField(blank=True, default='', max_length=128),
                ),
                ('input_tokens', models.PositiveIntegerField(default=0)),
                ('output_tokens', models.PositiveIntegerField(default=0)),
                ('attempts', models.PositiveSmallIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'thread',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='worker_usage_events',
                        to='aichat.chatthread',
                    ),
                ),
            ],
            options={
                'indexes': [
                    models.Index(
                        fields=['deployment', 'created_at'],
                        name='aichat_worker_usage_deploy_idx',
                    ),
                    models.Index(
                        fields=['purpose', 'created_at'],
                        name='aichat_worker_usage_purp_idx',
                    ),
                ]
            },
        )
    ]
