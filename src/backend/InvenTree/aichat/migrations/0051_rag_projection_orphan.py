"""Persist reverse projection findings independently of the native ingest ledger."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Dark schema only; no Search calls or feature activation."""

    dependencies = [('aichat', '0050_client_memory_erasure')]
    operations = [
        migrations.CreateModel(
            name='RagProjectionOrphan',
            fields=[
                (
                    'identity',
                    models.CharField(primary_key=True, max_length=64, serialize=False),
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
                ('index_name', models.CharField(max_length=128)),
                ('document_id', models.CharField(max_length=1024)),
                ('reason', models.CharField(max_length=32)),
                ('resolved', models.BooleanField(default=False, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        )
    ]
