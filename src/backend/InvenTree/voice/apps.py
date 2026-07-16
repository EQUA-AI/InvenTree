"""Application configuration for the Voice domain shell."""

from django.apps import AppConfig


class VoiceConfig(AppConfig):
    """Configure the Voice application shell."""

    default_auto_field = 'django.db.models.AutoField'
    name = 'voice'
    verbose_name = 'Voice'
