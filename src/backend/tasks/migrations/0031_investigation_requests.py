"""P41: local/offline canonical investigation request foundation."""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Install the local/offline investigation schema; never an Azure command."""

    dependencies = [
        ('tasks', '0030_workorder_tasks_wo_actual_completed_and_more'),
        ('repair', '0010_backfill_gate_blocking_authority'),
        ('common', '0053_merge_upstream_20260912'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='InvestigationRequest',
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
                    'work_order',
                    models.ForeignKey(
                        'tasks.workorder',
                        on_delete=models.PROTECT,
                        related_name='investigation_requests',
                    ),
                ),
                ('schema_version', models.PositiveIntegerField(default=1)),
                ('version', models.PositiveIntegerField(default=1)),
                ('purpose', models.TextField()),
                ('next_check', models.TextField()),
                (
                    'priority',
                    models.PositiveIntegerField(null=True, blank=True, default=None),
                ),
                ('required_artifacts', models.JSONField(default=list)),
                (
                    'status',
                    models.CharField(
                        max_length=24,
                        default='open',
                        choices=[
                            ('open', 'Open'),
                            ('answered', 'Answered'),
                            ('cannot_perform', 'Cannot perform'),
                            ('pending_external', 'Pending external'),
                            ('disputed', 'Disputed'),
                            ('resolved', 'Resolved'),
                        ],
                    ),
                ),
                (
                    'owner',
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        null=True,
                        blank=True,
                        on_delete=models.PROTECT,
                        related_name='+',
                    ),
                ),
                ('next_checkpoint', models.DateTimeField(null=True, blank=True)),
                (
                    'source_finding',
                    models.ForeignKey(
                        'repair.RepairInvestigationFinding',
                        null=True,
                        blank=True,
                        on_delete=models.PROTECT,
                        related_name='investigation_requests',
                    ),
                ),
                ('source_proposal', models.JSONField(null=True, blank=True)),
                ('blocked_reason', models.TextField(blank=True)),
                ('resolution', models.TextField(blank=True)),
                (
                    'created_by',
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.PROTECT,
                        related_name='+',
                    ),
                ),
                (
                    'updated_by',
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.PROTECT,
                        related_name='+',
                    ),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': [
                    models.OrderBy(models.F('priority'), nulls_last=True),
                    'created_at',
                    'pk',
                ],
                'indexes': [
                    models.Index(
                        fields=['work_order', 'status'],
                        name='tasks_inv_work_status_idx',
                    )
                ],
                'constraints': [
                    models.CheckConstraint(
                        condition=models.Q(
                            status__in=(
                                'open',
                                'answered',
                                'cannot_perform',
                                'pending_external',
                                'disputed',
                                'resolved',
                            )
                        ),
                        name='tasks_inv_request_status',
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name='InvestigationArtifact',
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
                    'request',
                    models.ForeignKey(
                        'tasks.investigationrequest',
                        on_delete=models.CASCADE,
                        related_name='artifacts',
                    ),
                ),
                (
                    'kind',
                    models.CharField(
                        max_length=24,
                        choices=[
                            ('observation', 'Observation'),
                            ('reading_set', 'Reading set'),
                            ('photo', 'Photo'),
                            ('report', 'Report'),
                            ('laboratory', 'Laboratory'),
                            ('check_result', 'Check result'),
                        ],
                    ),
                ),
                ('payload', models.JSONField()),
                ('attachment_snapshot', models.JSONField(null=True, blank=True)),
                (
                    'attachment',
                    models.ForeignKey(
                        'common.Attachment',
                        null=True,
                        blank=True,
                        on_delete=models.SET_NULL,
                        related_name='+',
                    ),
                ),
                (
                    'supersedes',
                    models.ForeignKey(
                        'tasks.investigationartifact',
                        null=True,
                        blank=True,
                        on_delete=models.RESTRICT,
                        related_name='corrections',
                    ),
                ),
                (
                    'recorded_by',
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.PROTECT,
                        related_name='+',
                    ),
                ),
                ('source_turn_id', models.CharField(max_length=80, blank=True)),
                ('source_proposal_id', models.UUIDField(null=True, blank=True)),
                ('source_file', models.JSONField(null=True, blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'constraints': [
                    models.CheckConstraint(
                        condition=models.Q(
                            kind__in=(
                                'observation',
                                'reading_set',
                                'photo',
                                'report',
                                'laboratory',
                                'check_result',
                            )
                        ),
                        name='tasks_inv_artifact_kind',
                    )
                ]
            },
        ),
    ]
