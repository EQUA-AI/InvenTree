"""Choices-only migration: register the ``di_read`` extractor value (R3).

The image pipeline stamps OCR provenance (``prebuilt-read``) on ingest rows.
Django records the widened choices in migration state; PostgreSQL emits no
DDL for a choices change, so this is dark-safe on the shared server by
construction.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Widen AttachmentIngest.extractor choices with di_read (no DDL)."""

    dependencies = [
        ("aichat", "0021_retrievalmiss_corpus_part_filter"),
    ]

    operations = [
        migrations.AlterField(
            model_name="attachmentingest",
            name="extractor",
            field=models.CharField(
                blank=True,
                choices=[
                    ("di_layout", "Document Intelligence layout"),
                    ("di_read", "Document Intelligence read (OCR)"),
                    ("direct", "Direct text read"),
                    ("pypdf_override", "pypdf (explicit override)"),
                ],
                default="",
                max_length=16,
            ),
        ),
    ]
