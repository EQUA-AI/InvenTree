"""Inspect deletion residuals without deleting or repairing content."""

import json

from django.core.management.base import BaseCommand, CommandError

from aichat.services.retention_audit import audit_deleted_threads


class Command(BaseCommand):
    """A bounded thread-deletion audit; recording is explicitly requested."""

    help = 'Probe recent deleted threads; no repairs. --record saves count-only evidence for retention_status.'

    def add_arguments(self, parser):
        """Expose sample bounds and an explicit evidence-recording option."""
        parser.add_argument('--sample', type=int, default=20)
        parser.add_argument('--record', action='store_true')
        parser.add_argument(
            '--fail-on-incomplete',
            action='store_true',
            help='Exit nonzero for residuals, unknown/error probes or no sampled deletions.',
        )

    def handle(self, *args, **options):
        """Emit only aggregate counts, registered kind/model names and times."""
        try:
            report = audit_deleted_threads(
                sample=options['sample'], record=options['record']
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        if options['fail_on_incomplete'] and report['status'] != 'clean_sample':
            raise CommandError('Deletion audit has incomplete or missing evidence')
