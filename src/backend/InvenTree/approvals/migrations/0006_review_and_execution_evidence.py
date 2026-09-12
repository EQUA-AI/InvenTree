"""Persist revision-bound review delivery, acknowledgment and execution evidence."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add evidence tables without changing existing approval rows."""

    dependencies = [
        ('approvals', '0005_alter_approval_action_type'),
        ('voice', '0005_voiceoperation'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ApprovalExecution',
            fields=[
                (
                    'idempotency_key',
                    models.CharField(max_length=64, primary_key=True, serialize=False),
                ),
                ('revision', models.PositiveIntegerField()),
                ('review_hash', models.CharField(max_length=64)),
                ('state', models.CharField(default='submitting', max_length=32)),
                ('result', models.JSONField(default=dict)),
                ('detail', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'actor',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'approval',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='executions',
                        to='approvals.approval',
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name='ApprovalReviewAcknowledgment',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('revision', models.PositiveIntegerField()),
                ('review_hash', models.CharField(max_length=64)),
                ('scope_hash', models.CharField(max_length=64)),
                ('sections', models.JSONField(default=list)),
                ('channel', models.CharField(max_length=16)),
                ('invalidated_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'actor',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'approval',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='review_acknowledgments',
                        to='approvals.approval',
                    ),
                ),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=(
                            'approval',
                            'actor',
                            'revision',
                            'review_hash',
                            'scope_hash',
                        ),
                        name='unique_approval_review_binding',
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name='ApprovalReviewDelivery',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('revision', models.PositiveIntegerField()),
                ('review_hash', models.CharField(max_length=64)),
                ('scope_hash', models.CharField(max_length=64)),
                ('section_id', models.CharField(max_length=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'actor',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'approval',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='review_deliveries',
                        to='approvals.approval',
                    ),
                ),
                (
                    'utterance',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to='voice.voiceutterance',
                    ),
                ),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=('utterance', 'section_id'),
                        name='unique_approval_delivery_section',
                    )
                ]
            },
        ),
    ]
