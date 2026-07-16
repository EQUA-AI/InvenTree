"""Application configuration for durable AI chat persistence."""

from django.apps import AppConfig


class AIChatConfig(AppConfig):
    """Configure the durable AI chat application."""

    default_auto_field = 'django.db.models.AutoField'
    name = 'aichat'
    verbose_name = 'AI Chat'
