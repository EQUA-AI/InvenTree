"""Schema-only deletion-manifest checks; never touch a database at startup."""

from django.core import checks


def check_thread_derivatives(app_configs=None, **kwargs):
    """Every thread-keyed model must declare deletion or an explicit exception."""
    from aichat.services.retention import THREAD_DERIVATIVES

    return [
        checks.Error(
            f'Thread derivative {label} has no retention registration.',
            hint='Register purge and residual probes before writing this model, or document its lifecycle exemption.',
            id='aichat.E022',
        )
        for label in THREAD_DERIVATIVES.uncovered_models()
    ]


def register_retention_checks():
    """Install the check idempotently, including in reduced application settings."""
    checks.register(checks.Tags.models)(check_thread_derivatives)
