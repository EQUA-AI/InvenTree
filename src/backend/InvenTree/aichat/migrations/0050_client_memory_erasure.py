"""Durable client-memory offboarding intent, without deleting any data."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Schema only; no client is opted out or enrolled by this migration."""

    dependencies = [('aichat', '0049_thread_memory_delete_choice')]
    operations = [
        migrations.CreateModel(
            name='ClientMemoryErasure',
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
                ('client_id', models.PositiveBigIntegerField(unique=True)),
                ('client_code', models.SlugField(max_length=64)),
                ('client_created_at', models.DateTimeField()),
                ('requested_at', models.DateTimeField()),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=['client_code'], name='aichat_client_erasure_code'
                    )
                ]
            },
        )
    ]
