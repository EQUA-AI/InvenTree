"""Serial-match backfill: ingest provenance → proposed claims (S8b WP-C8).

Only an EXACTLY-ONE serial match may become a ``proposed`` row; ambiguous
and unmatched documents are listed in the migration report and left
unresolved (the A6 rule: provenance is never authority, and the backfill
never verifies anything). Dry-run by default; ``--yes`` writes.
"""

import json

from django.core.management.base import BaseCommand, CommandError

from aichat.management.applicability_cli import resolve_actor


class Command(BaseCommand):
    """Propose exact-machine claims from unique document-serial matches."""

    help = (
        'For every current document whose ingest asset_id matches EXACTLY '
        'one machine serial, create a proposed exact_machine claim. '
        'Ambiguous or unmatched serials are reported, never guessed. '
        'Dry-run by default; pass --yes to write. Nothing is verified.'
    )

    def add_arguments(self, parser):
        """CLI arguments."""
        parser.add_argument('--yes', action='store_true', help='Actually write rows')
        parser.add_argument('--by', required=True, help='Proposing username')
        parser.add_argument('--json', action='store_true', dest='as_json')

    def handle(self, *args, **options):
        """Match, report, and (with --yes) propose."""
        from aichat.models import (
            ApplicabilityState,
            ControlledDocument,
            ControlledDocumentApplicability,
        )
        from aichat.services import applicability
        from assets.models import AssetMachine

        actor = resolve_actor(options['by'])
        write = bool(options['yes'])

        documents = ControlledDocument.objects.filter(is_current=True).exclude(
            asset_id=''
        )
        report: dict = {
            'mode': 'write' if write else 'dry_run',
            'proposed': [],
            'already_claimed': [],
            'ambiguous': [],
            'unmatched': [],
        }
        for document in documents.order_by('pk'):
            serial = document.asset_id
            machines = list(
                AssetMachine.objects.filter(serial=serial).values_list('pk', flat=True)
            )
            entry = {
                'document_id': document.document_id,
                'revision': document.revision,
                'serial': serial,
                'machine_ids': machines,
            }
            if not machines:
                report['unmatched'].append(entry)
                continue
            if len(machines) > 1:
                report['ambiguous'].append(entry)
                continue
            existing = ControlledDocumentApplicability.objects.filter(
                document=document, kind='exact_machine', target_machine_id=machines[0]
            ).exclude(state=ApplicabilityState.REVOKED)
            if existing.exists():
                report['already_claimed'].append(entry)
                continue
            if write:
                try:
                    row = applicability.propose(
                        document=document,
                        kind='exact_machine',
                        actor=actor,
                        basis=(
                            f'backfill: ingest asset_id {serial!r} matches exactly '
                            'one machine serial'
                        ),
                        target_machine_id=machines[0],
                        target_serial=serial,
                    )
                except applicability.ApplicabilityError as exc:
                    raise CommandError(str(exc)) from exc
                entry['claim'] = row.pk
            report['proposed'].append(entry)

        if options['as_json']:
            self.stdout.write(json.dumps(report, indent=2))
            return
        mode = 'WROTE' if write else 'DRY RUN — would propose'
        self.stdout.write(f'{mode} {len(report["proposed"])} exact-machine claims')
        self.stdout.write(
            f'already claimed: {len(report["already_claimed"])}; '
            f'ambiguous: {len(report["ambiguous"])}; '
            f'unmatched: {len(report["unmatched"])}'
        )
        for entry in report['ambiguous']:
            self.stdout.write(
                self.style.WARNING(
                    f'  ambiguous serial {entry["serial"]!r} '
                    f'({entry["document_id"]}): machines {entry["machine_ids"]}'
                )
            )
        if not write:
            self.stdout.write('pass --yes to create the proposed rows')
