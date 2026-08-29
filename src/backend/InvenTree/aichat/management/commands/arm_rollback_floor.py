"""Arm the AIMMS rollback floor (§14 monotonic safety, human-gated).

Writes the durable floor marker. Once armed, the capability-profile system
check requires scope enforcement, the unsafe-shortcut guard, and fixture
isolation to hold at EVERY tier — attempting to disable them afterwards
fails ``manage.py check`` and startup loudly. There is deliberately no
disarm command: rollback below the floor is prohibited by design; undoing
it requires a deliberate, auditable database edit.
"""

from django.core.management.base import BaseCommand, CommandError

from aimms_capability import ROLLBACK_FLOOR, ROLLBACK_FLOOR_SETTING


class Command(BaseCommand):
    """Arm the rollback floor marker."""

    help = (
        'Arm the AIMMS rollback floor: scope enforcement, the unsafe-shortcut '
        'guard, and fixture isolation become mandatory at every capability '
        'tier. One-way by design; requires --yes.'
    )

    def add_arguments(self, parser):
        """Register arguments."""
        parser.add_argument(
            '--yes',
            action='store_true',
            help='Confirm arming (one-way; no disarm command exists).',
        )

    def handle(self, *args, **options):
        """Write the marker after explicit confirmation."""
        from common.models import InvenTreeSetting

        already = str(
            InvenTreeSetting.get_setting(ROLLBACK_FLOOR_SETTING, '')
        ).lower() in ('1', 'true', 'yes')
        if already:
            self.stdout.write('Rollback floor is already armed.')
            return
        if not options['yes']:
            raise CommandError(
                'Arming is one-way (no disarm command). Re-run with --yes to '
                f'require {", ".join(ROLLBACK_FLOOR)} at every tier.'
            )
        InvenTreeSetting.set_setting(ROLLBACK_FLOOR_SETTING, 'true', None)
        self.stdout.write(
            self.style.SUCCESS('Rollback floor ARMED: ' + ', '.join(ROLLBACK_FLOOR))
        )
