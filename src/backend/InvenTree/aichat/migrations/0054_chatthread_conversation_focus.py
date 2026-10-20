"""Thread conversation focus: durable JSON target + its own optimistic version.

Reversible: the reverse operation drops both columns and restores the exact
prior schema. No data backfill — every existing thread reads as "no focus"
at version 0 (the most restrictive state: the conversation must name its
target again).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add conversation_focus + focus_version to ChatThread."""

    dependencies = [
        ('aichat', '0053_controlled_projection_audit'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatthread',
            name='conversation_focus',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='chatthread',
            name='focus_version',
            field=models.PositiveBigIntegerField(default=0),
        ),
    ]
