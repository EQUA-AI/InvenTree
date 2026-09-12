"""Bind newly reviewed proposals to their canonical previews."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Old pending proposals need a fresh review before voice can use them."""

    dependencies = [('aichat', '0034_chatcompactionevent_directives_flagged')]
    operations = [
        migrations.AddField(
            model_name='chatactionproposal',
            name='preview_hash',
            field=models.CharField(max_length=64, blank=True, default='', db_default=''),
        )
    ]
