"""Engineering countersign for model/configuration claims (S8b WP-C8)."""

import json

from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError

from aichat.management.applicability_cli import claim_row, resolve_actor


class Command(BaseCommand):
    """Record the engineering countersign on a model/configuration claim."""

    help = (
        'Countersign one proposed model/configuration applicability claim. '
        'Requires aichat.countersign_document_applicability and a human '
        'distinct from both the proposer and the verifier.'
    )

    def add_arguments(self, parser):
        """CLI arguments: the claim and the countersigning engineer."""
        parser.add_argument('--claim', type=int, required=True)
        parser.add_argument('--by', required=True, help='Countersigning username')
        parser.add_argument('--json', action='store_true', dest='as_json')

    def handle(self, *args, **options):
        """Apply the countersign through the service."""
        from aichat.services import applicability

        actor = resolve_actor(options['by'])
        try:
            row = applicability.countersign(options['claim'], actor=actor)
        except (applicability.ApplicabilityError, PermissionDenied) as exc:
            raise CommandError(str(exc)) from exc
        if options['as_json']:
            self.stdout.write(json.dumps(claim_row(row), indent=2))
        elif row.state == 'verified':
            self.stdout.write(self.style.SUCCESS(f'claim {row.pk} is now VERIFIED'))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f'claim {row.pk} countersign recorded; awaiting verification'
                )
            )
