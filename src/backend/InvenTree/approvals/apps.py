"""Django app configuration for the Approvals module."""

from django.apps import AppConfig


class ApprovalsConfig(AppConfig):
    """Configuration for the AI Agent Approval Queue app."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'approvals'
    verbose_name = 'AI Agent Approvals'
