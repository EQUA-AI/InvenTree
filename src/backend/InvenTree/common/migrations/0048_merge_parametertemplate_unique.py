"""Merge migration: fork-local 0047 merge branch + upstream 0047_parametertemplate_unique."""

from django.db import migrations


class Migration(migrations.Migration):
    """Merge the fork migration branch with the upstream parameter uniqueness migration."""

    dependencies = [
        ('common', '0047_merge_20260712_2258'),
        ('common', '0047_parametertemplate_unique'),
    ]

    operations = []
