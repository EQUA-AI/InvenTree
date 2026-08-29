"""Engage (or inspect) the AI pilot-stop latch (S15, §15.4).

Any ONE stop-authority owner may stop the pilot; clearing requires all
five recorded approvals (``pilot_resume``). Works with
``FEATURE_AI_PILOT_STOP_LATCH`` dark — the flag arms only the admission
gate, so operators can drill the procedure before enablement.
"""

import json

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Engage the latch, or print its state with --status."""

    help = (
        'Engage the AI emergency stop (any one owner; content-free reason '
        'code required) or report its state with --status. Arm the flag '
        'only after verifying the shared cache backend on both planes.'
    )

    def add_arguments(self, parser):
        """Register the stop/inspect arguments."""
        from aichat.models import AIPilotStopReason, AIPilotStopRole

        parser.add_argument(
            '--status', action='store_true', help='Read-only state print'
        )
        parser.add_argument(
            '--reason-code', choices=[c[0] for c in AIPilotStopReason.choices]
        )
        parser.add_argument('--by', help='Username of the engaging owner')
        parser.add_argument(
            '--role', default='', choices=[c[0] for c in AIPilotStopRole.choices]
        )
        parser.add_argument(
            '--detail', default='', help='Codes/ids only, never content'
        )
        parser.add_argument('--json', action='store_true')

    def handle(self, *args, **options):
        """Engage or inspect."""
        from aichat.services import pilot_latch

        if options['status']:
            self._emit(pilot_latch.current_state(), options['json'])
            return

        if not options['reason_code']:
            raise CommandError('--reason-code is required (or use --status)')
        if not options['by']:
            raise CommandError('--by <username> is required')
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.filter(username=options['by']).first()
        if user is None:
            raise CommandError(f'unknown user {options["by"]!r}')
        if not user.has_perm('aichat.manage_pilot_stop'):
            # Procedural guard, not a security boundary: the shell is already
            # trusted. Warn loudly and proceed — a real stop must never wait
            # on a permissions errand.
            self.stderr.write(
                self.style.WARNING(
                    f'{user.username} lacks aichat.manage_pilot_stop; engaging anyway'
                )
            )

        pilot_latch.engage_latch(
            reason_code=options['reason_code'],
            source='manual',
            engaged_by=user,
            engaged_role=options['role'],
            detail=options['detail'],
        )
        state = pilot_latch.current_state()
        self._emit(state, options['json'])
        if not options['json']:
            self.stdout.write(
                self.style.WARNING(
                    'Latch ENGAGED. Clearing requires pilot_resume approvals '
                    f'from: {", ".join(state["missing_roles"])}'
                )
            )

    def _emit(self, state, as_json):
        if as_json:
            self.stdout.write(json.dumps(state, indent=2))
        else:
            self.stdout.write(
                f'latched={state["latched"]} reason={state["reason_code"] or "-"} '
                f'approvals={state["approvals"]} missing={state["missing_roles"]}'
            )
