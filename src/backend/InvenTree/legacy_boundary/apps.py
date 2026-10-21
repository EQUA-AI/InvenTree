"""Register the migration-only compatibility boundary."""

from django.apps import AppConfig


class LegacyBoundaryConfig(AppConfig):
    """Keep compatibility storage outside the active application's models."""

    name = 'legacy_boundary'
    default_auto_field = 'django.db.models.AutoField'
