"""O20 calibration traceability; local/offline only until deployment approval."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Append-only per-reading certificate revisions inside the Work boundary."""

    dependencies = [
        ('tasks', '0032_closeoutcapture_voice_unavailable_reason'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('common', '0053_merge_upstream_20260912'),
    ]

    operations = [
        migrations.CreateModel(
            name='CalibrationTraceability',
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
                ('revision', models.PositiveIntegerField()),
                ('reference_instrument_id', models.CharField(max_length=200)),
                ('certificate_number', models.CharField(max_length=200)),
                ('issuing_lab', models.CharField(max_length=200)),
                ('valid_until', models.DateField(blank=True, null=True)),
                ('certificate_snapshot', models.JSONField()),
                ('correction_reason', models.TextField(blank=True)),
                ('recorded_at', models.DateTimeField(auto_now_add=True)),
                (
                    'work_order',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='calibration_traceability',
                        to='tasks.workorder',
                    ),
                ),
                (
                    'reading',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='calibration_traceability',
                        to='tasks.closeoutreading',
                    ),
                ),
                (
                    'supersedes',
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name='correction',
                        to='tasks.calibrationtraceability',
                    ),
                ),
                (
                    'certificate',
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to='common.attachment',
                    ),
                ),
                (
                    'recorded_by',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'ordering': ['reading_id', '-revision'],
                'constraints': [
                    models.UniqueConstraint(
                        fields=('reading', 'revision'),
                        name='tasks_cal_reading_revision',
                    ),
                    models.CheckConstraint(
                        condition=models.Q(revision__gte=1),
                        name='tasks_cal_positive_revision',
                    ),
                ],
            },
        )
    ]
