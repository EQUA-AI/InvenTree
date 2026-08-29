"""S1 (analysis rail): additive, old-code-compatible thread scope columns.

Every existing thread keeps an empty payload at version 0, which the
service layer reads as ``legacy_unconfirmed`` — no data backfill, no
inference from historic prose (decision record Q3/improvement 4).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the durable active-analysis-scope columns to ChatThread."""

    dependencies = [
        ('aichat', '0023_attachment_extractor_ffmpeg'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatthread',
            name='analysis_scope',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='chatthread',
            name='analysis_scope_version',
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='chatthread',
            name='analysis_scope_hash',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]
