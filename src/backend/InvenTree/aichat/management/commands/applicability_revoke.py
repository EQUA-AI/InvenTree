"""Revoke one applicability claim (S8b WP-C8)."""

import json

from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError

from aichat.management.applicability_cli import claim_row, resolve_actor


class Command(BaseCommand):
    """Revoke one applicability claim with a recorded reason."""

    help = (
        'Revoke one applicability claim (proposed or verified). Requires '
        'aichat.verify_document_applicability; the row remains as audit.'
    )

    def add_arguments(self, parser):
        """CLI arguments: the claim, the reason, the revoking human."""
        parser.add_argument('--claim', type=int, required=True)
        parser.add_argument('--reason', required=True)
        parser.add_argument('--by', required=True, help='Revoking username')
        parser.add_argument('--json', action='store_true', dest='as_json')

    def handle(self, *args, **options):
        """Apply the revocation through the service."""
        from aichat.services import applicability

        actor = resolve_actor(options['by'])
        try:
            row = applicability.revoke(
                options['claim'], actor=actor, reason=options['reason']
            )
        except (applicability.ApplicabilityError, PermissionDenied) as exc:
            raise CommandError(str(exc)) from exc
        if options['as_json']:
            self.stdout.write(json.dumps(claim_row(row), indent=2))
        else:
            self.stdout.write(self.style.SUCCESS(f'claim {row.pk} REVOKED'))
