"""O15 local schema: original media and immutable accepted report revisions."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import tasks.services.capture_storage


class Migration(migrations.Migration):
    """No historical reports/timing are inferred or backfilled."""

    dependencies = [
        ('tasks', '0032_closeoutcapture_voice_unavailable_reason'),
        ('voice', '0011_voice_validation_timing'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CaptureOriginal',
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
                    'file',
                    models.FileField(
                        storage=tasks.services.capture_storage.original_storage,
                        upload_to=tasks.services.capture_storage.original_path,
                    ),
                ),
                ('filename', models.CharField(max_length=255)),
                ('content_type', models.CharField(max_length=100)),
                ('content_hash', models.CharField(max_length=64)),
                ('byte_size', models.PositiveBigIntegerField()),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('duration_ms', models.PositiveBigIntegerField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'work_order',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to='tasks.workorder',
                    ),
                ),
                (
                    'voice_capture',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='originals',
                        to='voice.voicecapturesession',
                    ),
                ),
                (
                    'artifact',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='originals',
                        to='tasks.investigationartifact',
                    ),
                ),
                (
                    'recorded_by',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name='AcceptedCloseoutReport',
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
                ('document', models.JSONField()),
                ('text', models.TextField()),
                ('content_hash', models.CharField(max_length=64)),
                ('accepted_at', models.DateTimeField()),
                (
                    'closeout',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='reports',
                        to='tasks.workordercloseout',
                    ),
                ),
                (
                    'previous',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to='tasks.acceptedcloseoutreport',
                    ),
                ),
                (
                    'amendment',
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to='tasks.closeoutamendment',
                    ),
                ),
                (
                    'accepted_by',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=('closeout', 'revision'),
                        name='tasks_report_revision_uniq',
                    )
                ]
            },
        ),
    ]
