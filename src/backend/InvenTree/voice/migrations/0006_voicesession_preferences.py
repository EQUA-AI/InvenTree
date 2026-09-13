"""Record new-session consent without fabricating acceptance on old rows."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add nullable-history-compatible session preference fields."""

    dependencies = [('voice', '0005_voiceoperation')]
    operations = [
        migrations.AddField(
            model_name='voicesession',
            name='consent_version',
            field=models.CharField(
                max_length=32, blank=True, default='', db_default=''
            ),
        ),
        migrations.AddField(
            model_name='voicesession',
            name='locale',
            field=models.CharField(max_length=16, default='en-US', db_default='en-US'),
        ),
        migrations.AddField(
            model_name='voicesession',
            name='voice',
            field=models.CharField(
                max_length=64, default='en-US-AvaNeural', db_default='en-US-AvaNeural'
            ),
        ),
    ]
