"""Read-only rollout inventory; never print message content or credential values."""

import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count

from aichat.models import ConnectedMailbox, MailSyncState
from approvals.models import Approval, ApprovalExecution


class Command(BaseCommand):
    """Expose backlog, coverage and legacy recovery dependencies for operators."""

    help = (
        'Audit connected mailboxes and legacy email execution dependencies (read-only).'
    )

    def handle(self, *args, **options):
        """Report metadata and counts without exposing addresses or secrets."""
        legacy = Approval.objects.filter(action_type='email', email_draft__isnull=True)
        result = {
            'enabled': settings.AGENT_EMAIL_ENABLED,
            'send_paused': settings.AGENT_EMAIL_SEND_PAUSED,
            'credential_keys_configured': bool(settings.AGENT_EMAIL_CREDENTIAL_KEYS),
            'message_id_domain_configured': bool(
                settings.AGENT_EMAIL_MESSAGE_ID_DOMAIN
            ),
            'accounts': list(
                ConnectedMailbox.objects.values(
                    'provider', 'enabled', 'send_enabled', 'receive_enabled', 'health'
                ).annotate(count=Count('pk'))
            ),
            'mailbox_operations': list(
                ApprovalExecution.objects
                .filter(approval__email_draft__isnull=False)
                .values('state')
                .annotate(count=Count('pk'))
            ),
            'sync': list(
                MailSyncState.objects.values('status', 'has_gap').annotate(
                    count=Count('pk')
                )
            ),
            'legacy_approvals': list(
                legacy.values('status').annotate(count=Count('pk'))
            ),
            'legacy_unresolved_executions': ApprovalExecution.objects
            .filter(approval__in=legacy)
            .exclude(state__in=['succeeded', 'failed_before_effect'])
            .count(),
            'legacy_environment_variable_names': sorted(
                key for key in os.environ if 'GMAIL' in key.upper()
            ),
        }
        self.stdout.write(json.dumps(result, indent=2))
