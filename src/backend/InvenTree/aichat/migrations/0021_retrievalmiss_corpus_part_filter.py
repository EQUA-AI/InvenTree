"""Additive R2 ledger columns: retrieval surface + part-narrowing outcome.

``corpus`` separates attachment-corpus rows from governed ones in rollups;
``part_filter`` mirrors the new tool's part narrowing. Both AddFields carry
``db_default`` so the columns keep a DATABASE-level default — the postgres
server is shared by aimms-experimental and aimms-dev, and the env still
running pre-R2 code must keep inserting ledger rows during the deploy
window (dark-safe rule). No backfill, no index.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add RetrievalMiss.corpus and RetrievalMiss.part_filter (additive)."""

    dependencies = [
        ("aichat", "0020_attachment_ingest_claimed_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="retrievalmiss",
            name="corpus",
            field=models.CharField(
                blank=True, db_default="governed", default="governed", max_length=32
            ),
        ),
        migrations.AddField(
            model_name="retrievalmiss",
            name="part_filter",
            field=models.CharField(
                blank=True, db_default="", default="", max_length=16
            ),
        ),
    ]
