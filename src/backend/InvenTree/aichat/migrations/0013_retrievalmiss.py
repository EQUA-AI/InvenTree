# S16 A7: persist the retrieval telemetry the corpus search already computes
# and discards — query metadata only, never answer text. Additive; reverse is
# a clean drop.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Create the RetrievalMiss ledger."""

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('aichat', '0012_controlleddocument_embedding_stamp'),
    ]

    operations = [
        migrations.CreateModel(
            name='RetrievalMiss',
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
                ('query', models.CharField(max_length=500)),
                ('hit_count', models.PositiveIntegerField(default=0)),
                ('top_score', models.FloatField(blank=True, null=True)),
                ('machine_filter', models.CharField(blank=True, default='', max_length=16)),
                ('document_class', models.CharField(blank=True, default='', max_length=128)),
                ('scope_key', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'user',
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
                'indexes': [
                    models.Index(
                        fields=['hit_count', 'created_at'],
                        name='aichat_retrmiss_hit_idx',
                    )
                ],
            },
        ),
    ]
