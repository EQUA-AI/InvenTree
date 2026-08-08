"""Add the machine knowledge profile JSONField (S25). Additive, reversible."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add AssetMachine.profile with an empty-dict default."""

    dependencies = [
        ('assets', '0010_remove_assetmachine_customer'),
    ]

    operations = [
        migrations.AddField(
            model_name='assetmachine',
            name='profile',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Structured machine knowledge (criticality, components, fault codes)',
                verbose_name='Knowledge Profile',
            ),
        ),
    ]
