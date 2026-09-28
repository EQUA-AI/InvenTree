"""Governed apply for the EQUA demo metrics session (work package C).

Requires the resolved mapping, the reviewed plan and a matching approved plan
hash; performs the approved initial effects in one database transaction and
reconciles them before the session is marked applied.
"""

from django.core.management.base import BaseCommand

from assets.demo_metrics import apply_service, planner
from assets.demo_metrics.cli import (
    CliError,
    load_inputs,
    reject_placeholders,
    resolve_actor,
)


class Command(BaseCommand):
    """Apply one approved plan atomically."""

    help = (
        'Apply an approved EQUA demo metrics plan to the connected database. '
        'Requires --approved-plan-sha256 matching the plan body hash.'
    )

    def add_arguments(self, parser):
        """Declare validated arguments."""
        parser.add_argument(
            '--fixture', required=True, help='Path to demo_fixture.json'
        )
        parser.add_argument(
            '--mapping', required=True, help='Path to the resolved target mapping JSON'
        )
        parser.add_argument(
            '--plan', required=True, help='Path to the approved plan artifact'
        )
        parser.add_argument(
            '--approved-plan-sha256',
            required=True,
            help='Approved SHA-256 of the plan body (integrity, not authorization)',
        )
        parser.add_argument(
            '--actor', required=True, help='Username of the trusted operator'
        )
        parser.add_argument(
            '--include-history',
            action='store_true',
            help='Also import the bounded synthetic history intervals',
        )

    def handle(self, *args, **options):
        """Run the governed apply."""
        reject_placeholders(options['approved_plan_sha256'])
        fixture, mapping = load_inputs(
            options['fixture'], options['mapping'], require_ready=True
        )
        try:
            plan = planner.load_plan(options['plan'])
        except planner.PlanError as exc:
            raise CliError(exc.code, str(exc)) from exc
        if plan['plan_hash'] != options['approved_plan_sha256']:
            raise CliError(
                'APPROVAL_MISMATCH',
                'Approved plan hash does not match the plan artifact',
            )
        actor = resolve_actor(options['actor'])
        try:
            result = apply_service.apply_session(
                fixture=fixture,
                mapping=mapping,
                plan=plan,
                actor=actor,
                include_history=options['include_history'],
            )
        except apply_service.ApplyError as exc:
            raise CliError(exc.code, str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f'applied session={result.session_key} created={result.created} '
                f'receipts={result.receipts}'
            )
        )
