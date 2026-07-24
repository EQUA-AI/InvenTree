"""Merge migration: fork parameter-unique merge branch + upstream 0048_notificationmessage_link."""

from django.db import migrations


class Migration(migrations.Migration):
    """Merge the fork migration branch with the upstream notification-link migration."""

    dependencies = [
        ('common', '0048_merge_parametertemplate_unique'),
        ('common', '0048_notificationmessage_link'),
    ]

    operations = []
