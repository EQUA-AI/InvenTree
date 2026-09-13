"""Keep pre-Phase-E transcript inserts compatible during rollout and rollback."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Old ORM versions omit the newly introduced replay key."""

    dependencies = [('voice', '0008_voicecapturesession_live_session_and_more')]

    operations = [
        migrations.AlterField(
            model_name='voicetranscriptrevision',
            name='source_turn_id',
            field=models.CharField(
                max_length=128, blank=True, default='', db_default=''
            ),
        )
    ]
