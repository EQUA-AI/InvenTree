"""Deployment checks for semantic consent; default-off startup is database-free."""

from django.core import checks


def check_memory_configuration(app_configs=None, **kwargs):
    """An enabled semantic service cannot outrun its schema or published notice."""
    from django.db import connection

    from ai.core.config import get_settings
    from aichat.models import ClientAISettings
    from aichat.services.memory_controls import NOTICE_COPY
    from aichat.services.memory_eligibility import notice_number

    try:
        config = get_settings()
        enabled = (
            config.feature_semantic_memory_extract_shadow
            or config.feature_semantic_memory_recall
        )
        if not enabled:
            return []
        if config.feature_semantic_memory_recall:
            from django.conf import settings

            from aichat.services.memory_recall import SUPPORTED_RESOLVERS

            if (
                getattr(settings, 'AIMMS_MAINTENANCE_SCOPE_RESOLVER', None)
                not in SUPPORTED_RESOLVERS
            ):
                raise ValueError('Memory recall resolver requires a SQL adapter')
        current = config.aimms_memory_notice_version
        if current not in NOTICE_COPY:
            raise ValueError('Notice text unavailable')
        if connection.vendor != 'postgresql':
            raise ValueError('Semantic memory requires PostgreSQL')
        for required in ClientAISettings.objects.filter(
            memory_enabled=True
        ).values_list('required_notice_version', flat=True):
            if notice_number(required) > notice_number(current):
                raise ValueError('Client notice unavailable')
    except Exception:
        return [
            checks.Error(
                'Semantic memory configuration, notice or enrollment cannot be validated.',
                hint='Keep semantic flags off until PostgreSQL schema and notice requirements are ready.',
                id='aichat.E023',
            )
        ]
    return []


def register_memory_checks():
    """No database queries occur while registering the check."""
    checks.register('aimms')(check_memory_configuration)
