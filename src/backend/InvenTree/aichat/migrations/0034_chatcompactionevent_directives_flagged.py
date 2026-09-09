"""M2 PR 4 (§8.5.2): ChatCompactionEvent.directives_flagged (additive, dark-safe).

One new counter column with ``db_default=0``: the previous worker revision
keeps inserting event rows without naming it during the dark window.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the natural-language directive flag count beside directives_stripped."""

    dependencies = [('aichat', '0033_aiworkerusageevent')]

    operations = [
        migrations.AddField(
            model_name='chatcompactionevent',
            name='directives_flagged',
            field=models.PositiveIntegerField(db_default=0, default=0),
        )
    ]
