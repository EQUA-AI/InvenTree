"""Record one restart approval on the pilot-stop latch (S15, Q43).

Each invocation records ONE role's approval; the latch clears only when
approvals exist for all five stop-authority roles. Approvals are
immutable rows — the durable record of who agreed to restart.
"""

import json

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Record one role's restart approval."""

    help = (
        'Record one stop-authority approval to restart AI service; the fifth '
        'distinct role clears the emergency stop.'
    )

    def add_arguments(self, parser):
        """Register the approval arguments."""
        from aichat.models import AIPilotStopRole

        parser.add_argument(
            '--role', required=True, choices=[c[0] for c in AIPilotStopRole.choices]
        )
        parser.add_argument(
            '--by', required=True, help='Username of the approving owner'
        )
        parser.add_argument(
            '--reference', default='', help='Content-free dossier/document reference'
        )
        parser.add_argument('--json', action='store_true')

    def handle(self, *args, **options):
        """Record the approval and report progress."""
        from django.contrib.auth import get_user_model

        from aichat.services import pilot_latch

        user = get_user_model().objects.filter(username=options['by']).first()
        if user is None:
            raise CommandError(f'unknown user {options["by"]!r}')
        try:
            state = pilot_latch.record_resume_approval(
                role=options['role'], approved_by=user, reference=options['reference']
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        if options['json']:
            self.stdout.write(json.dumps(state, indent=2))
            return
        if state.get('cleared') or not state['latched']:
            self.stdout.write(
                self.style.SUCCESS('All five approvals recorded — latch CLEARED.')
            )
        else:
            self.stdout.write(
                f'Approval recorded for {options["role"]}. '
                f'Still missing: {", ".join(state["missing_roles"])}'
            )
