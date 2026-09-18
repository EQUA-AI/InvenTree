"""Default-off consent schema; never infer enrollment from existing data."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add schema only; no clients, acknowledgements or notices are backfilled."""

    dependencies = [
        ('aichat', '0041_accounterasuretombstone'),
        ('assets', '0015_alter_machineanomaly_work_order'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(
            model_name='chatthread',
            name='memory_mode',
            field=models.CharField(
                choices=[
                    ('inherit', 'Use my default'),
                    ('extract', 'Save eligible memories'),
                    ('off', 'Do not save memories'),
                ],
                db_default='inherit',
                default='inherit',
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='chatthread',
            name='memory_through_sequence',
            field=models.PositiveBigIntegerField(db_default=0, default=0),
        ),
        migrations.AddConstraint(
            model_name='chatthread',
            constraint=models.CheckConstraint(
                condition=models.Q(memory_mode__in=['inherit', 'extract', 'off']),
                name='aichat_thread_memory_mode',
            ),
        ),
        migrations.CreateModel(
            name='ClientAISettings',
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
                    'memory_enabled',
                    models.BooleanField(db_default=False, default=False),
                ),
                (
                    'required_notice_version',
                    models.CharField(blank=True, default='', max_length=64),
                ),
                ('enabled_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'client',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='ai_settings',
                        to='assets.client',
                    ),
                ),
                (
                    'enabled_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'constraints': [
                    models.CheckConstraint(
                        condition=models.Q(memory_enabled=False)
                        | (
                            ~models.Q(required_notice_version='')
                            & models.Q(enabled_at__isnull=False)
                        ),
                        name='aichat_memory_enrollment_notice',
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name='MemoryNoticeAcknowledgement',
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
                ('notice_version', models.CharField(max_length=64)),
                ('acknowledged_at', models.DateTimeField(auto_now_add=True)),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='memory_notices',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=('user', 'notice_version'),
                        name='aichat_memory_notice_unique',
                    ),
                    models.CheckConstraint(
                        condition=~models.Q(notice_version=''),
                        name='aichat_memory_notice_nonempty',
                    ),
                ]
            },
        ),
        migrations.CreateModel(
            name='UserMemorySettings',
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
                ('opted_out', models.BooleanField(db_default=False, default=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'user',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='memory_settings',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
