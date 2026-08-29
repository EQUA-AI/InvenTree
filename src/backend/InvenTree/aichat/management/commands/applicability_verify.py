"""Verify / countersign / revoke applicability claims (S8b WP-C8).

Three commands share this module's shape; this one records the
maintenance-management verification. A missing permission is a hard
``CommandError`` — verification is a control, not a procedural guard.
"""

import json

from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError

from aichat.management.applicability_cli import claim_row, resolve_actor


class Command(BaseCommand):
    """Record one human verification on a proposed claim."""

    help = (
        'Verify one proposed applicability claim. Requires '
        'aichat.verify_document_applicability; the proposer can never verify '
        'their own claim. Model/configuration kinds stay proposed until the '
        'separate engineering countersign.'
    )

    def add_arguments(self, parser):
        """CLI arguments: the claim and the verifying human."""
        parser.add_argument('--claim', type=int, required=True)
        parser.add_argument('--by', required=True, help='Verifying username')
        parser.add_argument('--json', action='store_true', dest='as_json')

    def handle(self, *args, **options):
        """Apply the verification through the service."""
        from aichat.services import applicability

        actor = resolve_actor(options['by'])
        try:
            row = applicability.verify(options['claim'], actor=actor)
        except (applicability.ApplicabilityError, PermissionDenied) as exc:
            raise CommandError(str(exc)) from exc
        if options['as_json']:
            self.stdout.write(json.dumps(claim_row(row), indent=2))
        elif row.state == 'verified':
            self.stdout.write(self.style.SUCCESS(f'claim {row.pk} is now VERIFIED'))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f'claim {row.pk} verification recorded; awaiting the '
                    'engineering countersign'
                )
            )
