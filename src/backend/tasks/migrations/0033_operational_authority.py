"""O15 local/offline authority records; never applied to Azure by this task."""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Append-only facts with original provenance and explicit corrections."""

    dependencies = [
        ('tasks', '0032_closeoutcapture_voice_unavailable_reason'),
        ('assets', '0022_var_unit'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='OperationalAuthorityRecord',
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
                ('version', models.PositiveIntegerField()),
                (
                    'recorded_at',
                    models.DateTimeField(
                        default=django.utils.timezone.now, editable=False
                    ),
                ),
                ('observed_at', models.DateTimeField()),
                ('effective_from', models.DateTimeField()),
                ('effective_until', models.DateTimeField(blank=True, null=True)),
                ('source_reference', models.CharField(max_length=1000)),
                ('statement', models.TextField()),
                ('basis', models.JSONField(default=dict)),
                (
                    'recorded_by',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'work_order',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='+',
                        to='tasks.workorder',
                    ),
                ),
                (
                    'kind',
                    models.CharField(
                        choices=[
                            ('test_acceptance', 'Test acceptance'),
                            ('operating_permission', 'Operating permission'),
                            ('observed_return', 'Observed return'),
                            ('service_state', 'Duty / standby / redundancy'),
                        ],
                        max_length=24,
                    ),
                ),
                ('outcome', models.CharField(max_length=24)),
                ('details', models.JSONField(default=dict)),
                (
                    'target',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='+',
                        to='assets.assetmachine',
                    ),
                ),
                (
                    'supersedes',
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='correction',
                        to='tasks.operationalauthorityrecord',
                    ),
                ),
            ],
            options={
                'ordering': ['-version', '-pk'],
                'abstract': False,
                'permissions': [
                    (
                        'record_operating_authority',
                        'Can record operating authority and service state',
                    )
                ],
                'indexes': [
                    models.Index(
                        fields=['work_order', 'kind'], name='tasks_authority_work_kind'
                    )
                ],
                'constraints': [
                    models.CheckConstraint(
                        condition=models.Q(
                            kind__in=(
                                'test_acceptance',
                                'operating_permission',
                                'observed_return',
                                'service_state',
                            )
                        ),
                        name='tasks_authority_kind',
                    )
                ],
            },
        )
    ]
