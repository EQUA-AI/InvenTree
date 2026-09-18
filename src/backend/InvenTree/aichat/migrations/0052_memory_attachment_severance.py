"""Dark source-pointer restore proof; no content migration or live cleanup."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Independent identities survive deletion of source, claim, fact or owner."""

    dependencies = [('aichat', '0051_rag_projection_orphan')]
    operations = [
        migrations.CreateModel(
            name='MemoryAttachmentSeverance',
            fields=[
                (
                    'identity',
                    models.CharField(primary_key=True, max_length=64, serialize=False),
                ),
                ('claim_id', models.PositiveBigIntegerField()),
                ('claim_created_at', models.DateTimeField()),
                ('fact_id', models.UUIDField()),
                ('fact_created_at', models.DateTimeField()),
                ('owner_id', models.PositiveBigIntegerField()),
                ('owner_joined_at', models.DateTimeField()),
                ('attachment_id', models.PositiveBigIntegerField()),
                ('severed_at', models.DateTimeField(db_index=True)),
            ],
        )
    ]
