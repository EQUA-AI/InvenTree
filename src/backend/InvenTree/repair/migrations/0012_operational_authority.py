"""O15 local/offline authority records; never applied to Azure by this task."""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Append-only facts with original provenance and explicit corrections."""

    dependencies = [
        ('repair', '0011_repair_finding_revisions'),
        ('tasks', '0033_operational_authority'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='GateAuthorityRecord',
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
                            ('isolation', 'Isolation point'),
                            ('permit', 'Permit'),
                            ('access_window', 'Access window'),
                        ],
                        max_length=24,
                    ),
                ),
                (
                    'outcome',
                    models.CharField(
                        choices=[
                            ('active', 'Active'),
                            ('released', 'Released'),
                            ('revoked', 'Revoked'),
                        ],
                        max_length=24,
                    ),
                ),
                ('verified_at', models.DateTimeField(blank=True, null=True)),
                (
                    'verified_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'gate',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='authority_records',
                        to='repair.repairpacketgate',
                    ),
                ),
                (
                    'supersedes',
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='correction',
                        to='repair.gateauthorityrecord',
                    ),
                ),
            ],
            options={
                'ordering': ['-version', '-pk'],
                'abstract': False,
                'constraints': [
                    models.CheckConstraint(
                        condition=models.Q(
                            kind__in=('isolation', 'permit', 'access_window')
                        ),
                        name='repair_authority_kind',
                    )
                ],
            },
        )
    ]
