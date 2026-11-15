"""Offline deployment check: no SDK import, token issuance or source access."""

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integration_state.palantir_transport import (
    TransportError,
    sdk_distribution,
    validate_config,
)


class Command(BaseCommand):
    """Check explicit server configuration and exact installed SDK version."""

    help = 'Validate the optional Palantir OSDK configuration offline; no live verification'
    requires_system_checks = []

    def add_arguments(self, parser):
        """Allow deployment automation to require installation, while off is valid."""
        parser.add_argument('--require-configured', action='store_true')

    def handle(self, *args, **options):
        """Return metadata only; credentials and field policies are never resolved."""
        config = getattr(settings, 'AIMMS_PALANTIR_TRANSPORT', None)
        if config is None:
            if options['require_configured']:
                raise CommandError('Palantir transport is disabled')
            result = {'status': 'disabled', 'live_verified': False}
        else:
            try:
                validate_config(config)
                sdk_distribution(config)
            except (TransportError, OSError, ValueError, TypeError):
                raise CommandError(
                    'Palantir transport configuration or pinned generated SDK is unavailable'
                ) from None
            result = {
                'status': 'configured',
                'domains': [m.domain for m in config.mappings],
                'live_verified': False,
            }
        self.stdout.write(json.dumps(result, sort_keys=True))
