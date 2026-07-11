"""Add DB index for AssetMachine.location.

Location is used as a primary filter field for equipment assets.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='assetmachine',
            name='location',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text='Free-text location (e.g. "Bay 4", "Sydney")',
                max_length=255,
                verbose_name='Location',
            ),
        ),
    ]
