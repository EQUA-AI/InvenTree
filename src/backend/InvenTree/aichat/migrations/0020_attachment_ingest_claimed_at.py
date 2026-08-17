"""Additive claim-fence timestamp for the attachment-RAG registry (hardening pass).

``claimed_at`` is written only by the atomic ingest claim (and renewed by the
indexed short-circuit); winner/loser resolution orders on it instead of
``created_at``, which inverts on content reverts. Nullable AddField only —
dark-safe on the shared PG server, no backfill, no index.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add AttachmentIngest.claimed_at (nullable, additive-only)."""

    dependencies = [
        ('aichat', '0019_attachment_rag_skip_extractor'),
    ]

    operations = [
        migrations.AddField(
            model_name='attachmentingest',
            name='claimed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
