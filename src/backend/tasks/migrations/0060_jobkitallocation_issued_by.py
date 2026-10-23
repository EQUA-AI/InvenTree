"""Retain the actual issue actor; historical unknown actors remain null."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add nullable attribution without inventing historical issue actors."""

    dependencies = [
        ('tasks', '0050_maintenance_receipts'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='jobkitallocation',
            name='issued_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='+',
                to=settings.AUTH_USER_MODEL,
            ),
        )
    ]
