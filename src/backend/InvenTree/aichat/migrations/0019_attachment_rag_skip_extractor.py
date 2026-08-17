"""R1 additive registry delta: `skipped` router state + `extractor` provenance.

Dark-safe on the shared PG server: one nullable-equivalent AddField with a
server-side-irrelevant default and a choices-only AlterField (no DDL beyond
the new column). Decisions #10/#12.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add AttachmentIngest.extractor and the skipped state choice."""

    dependencies = [('aichat', '0018_attachment_rag_registry')]

    operations = [
        migrations.AddField(
            model_name='attachmentingest',
            name='extractor',
            field=models.CharField(
                blank=True,
                choices=[
                    ('di_layout', 'Document Intelligence layout'),
                    ('direct', 'Direct text read'),
                    ('pypdf_override', 'pypdf (explicit override)'),
                ],
                default='',
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name='attachmentingest',
            name='state',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('extracting', 'Extracting'),
                    ('embedding', 'Embedding'),
                    ('indexed', 'Indexed'),
                    ('failed', 'Failed'),
                    ('superseded', 'Superseded'),
                    ('deleted', 'Deleted'),
                    ('skipped', 'Skipped'),
                ],
                db_index=True,
                default='pending',
                max_length=16,
            ),
        ),
    ]
