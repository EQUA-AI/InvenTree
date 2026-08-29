"""The applicability operational report (S8b WP-C8)."""

import json

from django.core.management.base import BaseCommand

from aichat.management.applicability_cli import claim_row


class Command(BaseCommand):
    """Report claim counts, the pending queue, and stale-hash verifications."""

    help = (
        'Report applicability claims: counts by state and kind, the queue '
        'awaiting human verification/countersign, and verified rows whose '
        'document bytes have changed since verification (stale — no longer '
        'served).'
    )

    def add_arguments(self, parser):
        """CLI arguments."""
        parser.add_argument('--json', action='store_true', dest='as_json')

    def handle(self, *args, **options):
        """Assemble and print the report."""
        from django.db.models import Count, F

        from aichat.models import ApplicabilityState, ControlledDocumentApplicability

        rows = ControlledDocumentApplicability.objects.select_related(
            'document', 'proposed_by', 'verified_by', 'countersigned_by'
        )
        by_state = {
            entry['state']: entry['n']
            for entry in rows.values('state').annotate(n=Count('pk')).order_by('state')
        }
        by_kind = {
            entry['kind']: entry['n']
            for entry in rows
            .filter(state=ApplicabilityState.VERIFIED)
            .values('kind')
            .annotate(n=Count('pk'))
            .order_by('kind')
        }
        pending = [
            claim_row(claim)
            for claim in rows.filter(state=ApplicabilityState.PROPOSED).order_by('pk')
        ]
        stale = [
            claim_row(claim)
            for claim in rows
            .filter(state=ApplicabilityState.VERIFIED)
            .exclude(document_content_sha256=F('document__source_sha256'))
            .order_by('pk')
        ]
        report = {
            'by_state': by_state,
            'verified_by_kind': by_kind,
            'pending': pending,
            'stale_hash': stale,
        }
        if options['as_json']:
            self.stdout.write(json.dumps(report, indent=2))
            return
        self.stdout.write(f'claims by state: {by_state or "none"}')
        self.stdout.write(f'verified by kind: {by_kind or "none"}')
        self.stdout.write(f'awaiting human action: {len(pending)}')
        for entry in pending:
            self.stdout.write(
                f'  claim {entry["claim"]}: {entry["document_id"]} r{entry["revision"]} '
                f'{entry["kind"]} (proposed by {entry["proposed_by"]})'
            )
        if stale:
            self.stdout.write(
                self.style.WARNING(
                    f'{len(stale)} verified claims are byte-stale (document '
                    'content changed since verification) and are NOT served:'
                )
            )
            for entry in stale:
                self.stdout.write(
                    f'  claim {entry["claim"]}: {entry["document_id"]} '
                    f'r{entry["revision"]} — re-verify against the new bytes'
                )
