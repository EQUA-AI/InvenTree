"""Ownership-aware cleanup for the EQUA demo metrics session (work package H).

Defaults to a read-only deletion/retention plan. Mutation requires the separate
approved cleanup hash of the exact plan body computed at apply time.
"""

from django.core.management.base import BaseCommand

from assets.demo_metrics import cleanup
from assets.demo_metrics.cli import (
    CliError,
    authorize_operator,
    dump,
    reject_placeholders,
    resolve_actor,
    resolve_session,
)


class Command(BaseCommand):
    """Plan (default) or apply a reviewed targeted cleanup."""

    help = (
        'Read-only cleanup/retention plan by default; --apply requires '
        '--approved-cleanup-sha256 matching the current plan body.'
    )

    def add_arguments(self, parser):
        """Declare validated arguments."""
        parser.add_argument('--session', required=True, help='Session key')
        parser.add_argument(
            '--actor', required=True, help='Username of the trusted operator'
        )
        parser.add_argument('--out', help='Path for the cleanup plan artifact')
        parser.add_argument(
            '--apply', action='store_true', help='Apply the approved cleanup'
        )
        parser.add_argument(
            '--approved-cleanup-sha256',
            help='Approved SHA-256 of the cleanup plan body (with --apply)',
        )

    def handle(self, *args, **options):
        """Plan or apply; refuse ambiguous invocations."""
        actor = resolve_actor(options['actor'])
        authorize_operator(actor)
        session = resolve_session(options['session'])
        if not options['apply']:
            try:
                plan = cleanup.build_cleanup_plan(session, actor)
            except cleanup.CleanupError as exc:
                raise CliError(exc.code, exc.message) from exc
            if options['out']:
                dump(options['out'], plan)
            self.stdout.write(
                self.style.SUCCESS(
                    f'cleanup_plan_hash={plan["plan_hash"]} '
                    f'deletions={len(plan["deletions"])} '
                    f'retained_modified={len(plan["retained_modified"])}'
                )
            )
            return
        approved = options['approved_cleanup_sha256']
        if not approved:
            raise CliError('BAD_ARGUMENT', '--apply requires --approved-cleanup-sha256')
        reject_placeholders(approved)
        try:
            result = cleanup.apply_cleanup(
                session, actor, approved_cleanup_sha256=approved
            )
        except cleanup.CleanupError as exc:
            raise CliError(exc.code, str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f'cleanup={result}'))
