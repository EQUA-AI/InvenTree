"""Application registration for durable source integration bookkeeping."""

from django.apps import AppConfig


class IntegrationStateConfig(AppConfig):
    """Own internal change and acknowledgement metadata without auto-enrollment."""

    name = 'integration_state'
    verbose_name = 'Source integration state'
    default_auto_field = 'django.db.models.BigAutoField'
