"""Read-only verification for the EQUA demo metrics session (work packages B/H).

The actor is authorized *before* any receipt, record or metric is read. The
report reads back receipts, records, scope and counts through the database and
the scoped services, and reports operator edits as drift to preserve — never
as values to overwrite. A successful process exit is not evidence; this report
is, and any acceptance failure (authorization, scope, drift or unavailable
metrics) is machine-readable and exits non-zero.
"""

import json

from django.core.management.base import BaseCommand

from assets.demo_metrics import cleanup, cohort
from assets.demo_metrics.cli import (
    CliError,
    authorize_operator,
    resolve_actor,
    resolve_session,
)

#: Maximum receipts returned in the report page (bounded, not the full set).
RECEIPT_PAGE_LIMIT = 200


class Command(BaseCommand):
    """Verify one applied session and print a read-only report."""

    help = 'Read-only: verify receipts, records, scope and counts of a demo session.'

    def add_arguments(self, parser):
        """Declare validated arguments."""
        parser.add_argument('--session', required=True, help='Session key to verify')
        parser.add_argument(
            '--actor', required=True, help='Username of the trusted operator'
        )

    def handle(self, *args, **options):
        """Build the verification report; refuse on any acceptance failure."""
        actor = resolve_actor(options['actor'])
        # Authorization first: nothing is read for an unauthorized actor.
        authorize_operator(actor)

        session = resolve_session(options['session'])
        # Scope boundary first, over every session membership — inactive
        # claims included. It runs before any receipt, ledger or metric read
        # and before any report output: a denied actor receives no report
        # data at all, only the machine-readable denial.
        try:
            cleanup._require_actor_scope(actor, session)
        except cleanup.CleanupError as exc:
            raise CliError(exc.code, exc.message) from exc
        failures = []

        from assets.demo_metrics_models import DemoMetricsObject

        receipts = list(
            session.receipts.order_by('operation_kind', 'item_key').values(
                'operation_kind', 'item_key', 'outcome', 'request_hash', 'effect_ids'
            )[:RECEIPT_PAGE_LIMIT]
        )
        counts = {
            kind: session.ledger_objects.filter(kind=kind).count()
            for kind in DemoMetricsObject.Kind.values
        }
        drift = []
        for row in session.ledger_objects.order_by('kind', 'fixture_key'):
            current = cleanup.object_fingerprint(row)
            if row.seed_fingerprint and current != row.seed_fingerprint:
                drift.append({
                    'kind': row.kind,
                    'key': row.fixture_key,
                    'status': 'operator_edit_preserved',
                })
        if drift:
            failures.append({
                'code': 'OPERATOR_DRIFT',
                'detail': f'{len(drift)} record(s)',
            })

        try:
            metrics = cohort.session_current_metrics(session, actor)
        except cohort.CohortError as exc:
            metrics = {'unavailable': exc.code}
            failures.append({'code': 'METRICS_UNAVAILABLE', 'detail': exc.code})

        report = {
            'session': {
                'dataset_key': session.dataset_key,
                'session_key': session.session_key,
                'status': session.status,
                'plan_sha256': session.plan_sha256,
                'fixture_canonical_sha256': session.fixture_canonical_sha256,
                'mapping_sha256': session.mapping_sha256,
                'target_fingerprint': session.target_fingerprint,
                'applied_at': session.applied_at,
            },
            # The bounded page is distinct from the durable total.
            'receipt_count': session.receipts.count(),
            'receipts_returned': len(receipts),
            'receipts': receipts,
            'ledger_counts': counts,
            'drift': drift,
            'metrics': metrics,
            'acceptance': {'ok': not failures, 'failures': failures},
        }
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True, default=str))
        if failures:
            raise CliError(
                'VERIFY_FAILED',
                'acceptance failures: '
                + ', '.join(failure['code'] for failure in failures),
            )
        self.stdout.write(self.style.SUCCESS('verification passed'))
