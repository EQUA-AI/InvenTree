"""Propose one document-applicability claim (S8b WP-C8).

Creates a ``proposed`` row only — verification is a separate human act by
a different person (``applicability_verify`` / ``applicability_countersign``).
"""

import json

from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError

from aichat.management.applicability_cli import (
    claim_row,
    resolve_actor,
    resolve_document,
)


class Command(BaseCommand):
    """Create one proposed applicability claim for a document revision."""

    help = (
        'Propose that one controlled-document revision applies to equipment. '
        'The claim starts (and stays) proposed until a different human '
        'verifies it.'
    )

    def add_arguments(self, parser):
        """CLI arguments: the document triple, the kind, one target, the human."""
        parser.add_argument('--scope-key', required=True)
        parser.add_argument('--document-id', required=True)
        parser.add_argument('--revision', required=True)
        parser.add_argument(
            '--kind',
            required=True,
            choices=[
                'exact_machine',
                'inverter_model',
                'firmware_config',
                'fleet_wide',
            ],
        )
        parser.add_argument('--machine-id', type=int, default=0)
        parser.add_argument('--serial', default='')
        parser.add_argument('--model', default='')
        parser.add_argument('--config', default='', help='JSON constraints payload')
        parser.add_argument('--basis', required=True, help='Evidence for the claim')
        parser.add_argument('--effective-from', default=None)
        parser.add_argument('--effective-to', default=None)
        parser.add_argument('--by', required=True, help='Proposing username')
        parser.add_argument('--json', action='store_true', dest='as_json')

    def handle(self, *args, **options):
        """Create the proposed row and print it."""
        from aichat.services import applicability

        actor = resolve_actor(options['by'])
        document = resolve_document(
            scope_key=options['scope_key'],
            document_id=options['document_id'],
            revision=options['revision'],
        )
        config = {}
        if options['config']:
            try:
                config = json.loads(options['config'])
            except json.JSONDecodeError as exc:
                raise CommandError(f'--config is not valid JSON: {exc}') from exc
        try:
            row = applicability.propose(
                document=document,
                kind=options['kind'],
                actor=actor,
                basis=options['basis'],
                target_machine_id=options['machine_id'],
                target_serial=options['serial'],
                target_model=options['model'],
                target_config=config,
                effective_from=options['effective_from'] or None,
                effective_to=options['effective_to'] or None,
            )
        except (applicability.ApplicabilityError, PermissionDenied) as exc:
            raise CommandError(str(exc)) from exc
        if options['as_json']:
            self.stdout.write(json.dumps(claim_row(row), indent=2))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'proposed claim {row.pk} ({row.kind}) — awaiting verification'
                )
            )
