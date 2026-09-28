"""Read-only planning for the EQUA demo metrics session (work package B).

Discovery, mapping validation, expected effects/conflicts and canonical plan
output. This command performs no database writes and no simulated writes
followed by rollback; the plan artifact is a local file that apply requires.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from assets.demo_metrics import planner
from assets.demo_metrics.cli import (
    CliError,
    authorize_operator,
    load_inputs,
    resolve_actor,
)


class Command(BaseCommand):
    """Produce a canonical, hashable plan for one resolved mapping."""

    help = (
        'Read-only: validate the EQUA demo fixture/mapping against the live '
        'database and write a canonical plan artifact. Never writes to the '
        'database.'
    )

    def add_arguments(self, parser):
        """Declare validated arguments."""
        parser.add_argument(
            '--fixture', required=True, help='Path to demo_fixture.json'
        )
        parser.add_argument(
            '--mapping', required=True, help='Path to the resolved target mapping JSON'
        )
        parser.add_argument('--out', required=True, help='Path for the plan artifact')
        parser.add_argument(
            '--actor', required=True, help='Username of the trusted operator'
        )

    def handle(self, *args, **options):
        """Build and write the canonical plan."""
        actor = resolve_actor(options['actor'])
        # Authorization first: nothing is read or written for an
        # unauthorized actor.
        authorize_operator(actor)
        fixture, mapping = load_inputs(
            options['fixture'], options['mapping'], require_ready=False
        )
        resolutions, conflicts = planner.resolve_targets(fixture, mapping, actor)
        body = planner.build_plan(
            fixture=fixture,
            mapping=mapping,
            actor=actor,
            session_anchor=timezone.now(),
            resolutions=resolutions,
            conflicts=conflicts,
        )
        try:
            plan_hash = planner.write_plan(options['out'], body)
        except planner.PlanError as exc:
            # Scope-denied plans are refused in the service before any
            # artifact exists; surface the machine-readable denial only.
            raise CliError(exc.code, exc.message) from exc
        except FileExistsError as exc:
            raise CliError('PLAN_EXISTS', 'Plan output exists; use a new path') from exc
        self.stdout.write(
            self.style.SUCCESS(
                f'plan_sha256={plan_hash} conflicts={len(conflicts)} '
                f'effects={len(body["effects"]["create"])}'
            )
        )
        if conflicts:
            self.stdout.write(self.style.WARNING(f'conflicts={conflicts}'))
