"""Projection evidence, repair obligations and recall stop latches; no activation."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Schema only; audit/provider flags stay off."""

    dependencies = [('aichat', '0047_memory_tombstone_owner_identity')]
    operations = [
        migrations.CreateModel(
            name='RagProjectionAudit',
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
                    'corpus',
                    models.CharField(
                        max_length=16,
                        choices=[
                            ('attachment', 'Attachment documents'),
                            ('media', 'Evidence media'),
                        ],
                    ),
                ),
                ('outcome', models.CharField(max_length=16)),
                ('sampled', models.PositiveIntegerField(default=0)),
                ('drift', models.PositiveIntegerField(default=0)),
                ('critical', models.PositiveIntegerField(default=0)),
                ('errors', models.PositiveIntegerField(default=0)),
                ('truncated', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
        ),
        migrations.CreateModel(
            name='RagProjectionGate',
            fields=[
                (
                    'corpus',
                    models.CharField(
                        primary_key=True,
                        serialize=False,
                        max_length=16,
                        choices=[
                            ('attachment', 'Attachment documents'),
                            ('media', 'Evidence media'),
                        ],
                    ),
                ),
                ('blocked', models.BooleanField(default=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='RagProjectionRepair',
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
                    'ingest',
                    models.OneToOneField(
                        to='aichat.attachmentingest', on_delete=models.CASCADE
                    ),
                ),
                (
                    'corpus',
                    models.CharField(
                        max_length=16,
                        choices=[
                            ('attachment', 'Attachment documents'),
                            ('media', 'Evidence media'),
                        ],
                    ),
                ),
                ('reason', models.CharField(max_length=16)),
                ('snapshot_hash', models.CharField(max_length=64)),
                ('resolved', models.BooleanField(default=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
