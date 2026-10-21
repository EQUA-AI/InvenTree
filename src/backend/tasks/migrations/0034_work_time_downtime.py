"""O15 attributed facts; local schema only, no legacy facts inferred."""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add recording providers without backfilling unknown facts."""

    dependencies = [
        ('tasks', '0032_closeoutcapture_voice_unavailable_reason'),
        ('assets', '0023_registry_recorded_facts'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='WorkTimeInterval',
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
                        on_delete=models.PROTECT,
                        related_name='time_intervals',
                        to='tasks.workorder',
                    ),
                ),
                (
                    'person',
                    models.ForeignKey(
                        on_delete=models.PROTECT,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ('started_at', models.DateTimeField()),
                ('ended_at', models.DateTimeField(null=True, blank=True)),
                ('end_action', models.CharField(max_length=8, blank=True)),
                (
                    'recorded_by',
                    models.ForeignKey(
                        on_delete=models.PROTECT,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ('recorded_at', models.DateTimeField(auto_now_add=True)),
                (
                    'ended_by',
                    models.ForeignKey(
                        null=True,
                        blank=True,
                        on_delete=models.PROTECT,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ('ended_recorded_at', models.DateTimeField(null=True, blank=True)),
                ('reason', models.TextField(blank=True)),
                (
                    'supersedes',
                    models.OneToOneField(
                        null=True,
                        blank=True,
                        on_delete=models.PROTECT,
                        related_name='replacement',
                        to='tasks.worktimeinterval',
                    ),
                ),
            ],
            options={
                'indexes': [
                    models.Index(
                        fields=['work_order', 'person'], name='work_time_person'
                    )
                ],
                'constraints': [
                    models.CheckConstraint(
                        condition=models.Q(ended_at__isnull=True)
                        | models.Q(ended_at__gt=models.F('started_at')),
                        name='work_time_positive',
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name='WorkDowntime',
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
                        on_delete=models.PROTECT,
                        related_name='downtime_records',
                        to='tasks.workorder',
                    ),
                ),
                (
                    'binding',
                    models.ForeignKey(
                        on_delete=models.PROTECT, to='assets.machinesignalbinding'
                    ),
                ),
                ('started_at', models.DateTimeField()),
                ('ended_at', models.DateTimeField()),
                ('proposal', models.JSONField()),
                (
                    'recorded_by',
                    models.ForeignKey(
                        on_delete=models.PROTECT, to=settings.AUTH_USER_MODEL
                    ),
                ),
                ('recorded_at', models.DateTimeField(auto_now_add=True)),
                ('reason', models.TextField(blank=True)),
                (
                    'supersedes',
                    models.OneToOneField(
                        null=True,
                        blank=True,
                        on_delete=models.PROTECT,
                        related_name='replacement',
                        to='tasks.workdowntime',
                    ),
                ),
            ],
            options={},
        ),
    ]
