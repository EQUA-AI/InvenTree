"""Run the S16/Q48 retention purges on demand (paired with the daily task).

The scheduled task (``aichat.tasks.run_retention_purge``) runs the same
service functions daily behind FEATURE_AI_RETENTION_JOBS; this command
exists for dry-run inspection, operator drills while the flag is dark,
and targeted per-family runs. Destructive runs require --yes.
"""

import json

from django.core.management.base import BaseCommand, CommandError

#: Families whose purge window accepts a --days-override (the rest have
#: non-day knobs: uploads TTL hours, aggregate months, outbox batches).
DAYS_OVERRIDABLE = (
    'threads',
    'voice',
    'proposals',
    'usage_detail',
    'retrieval_misses',
    'rejections',
    'quota_reservations',
    'quota_audit',
    'tombstones',
)


class Command(BaseCommand):
    """Run (or dry-run) the retention purge families."""

    help = (
        'Run the S16 retention purges. --dry-run reports eligible counts '
        'with zero writes; destructive runs require --yes and work even '
        'while FEATURE_AI_RETENTION_JOBS is dark (with a warning).'
    )

    def add_arguments(self, parser):
        """CLI options."""
        from aichat.services.retention import FAMILIES

        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report per-family eligible counts; delete nothing.',
        )
        parser.add_argument(
            '--family',
            action='append',
            choices=sorted(FAMILIES),
            help='Limit to one or more families (repeatable; default all).',
        )
        parser.add_argument(
            '--days-override',
            type=int,
            default=None,
            help='Override the retention window; valid with exactly one --family.',
        )
        parser.add_argument(
            '--json', action='store_true', help='Emit machine-readable JSON'
        )
        parser.add_argument(
            '--yes',
            action='store_true',
            help='Confirm a destructive run (required unless --dry-run).',
        )

    def handle(self, *args, **options):
        """Dispatch to the retention service and report."""
        from django.conf import settings as django_settings

        from aichat.services import retention

        dry_run = options['dry_run']
        families = set(options['family']) if options['family'] else None
        override = options['days_override']

        if not dry_run and not options['yes']:
            raise CommandError(
                'Destructive run: pass --yes to confirm, or use --dry-run.'
            )
        if not dry_run and not getattr(
            django_settings, 'FEATURE_AI_RETENTION_JOBS', False
        ):
            self.stderr.write(
                self.style.WARNING(
                    'FEATURE_AI_RETENTION_JOBS is dark — proceeding as an '
                    'operator on-demand run.'
                )
            )

        if override is not None:
            if families is None or len(families) != 1:
                raise CommandError('--days-override requires exactly one --family.')
            family = next(iter(families))
            if family not in DAYS_OVERRIDABLE:
                raise CommandError(
                    f'--days-override does not apply to {family!r} '
                    f'(valid: {", ".join(DAYS_OVERRIDABLE)}).'
                )
            if override < 1:
                raise CommandError('--days-override must be at least 1.')
            result = retention.FAMILIES[family](dry_run=dry_run, days=override)
            report = {
                'dry_run': dry_run,
                'days_override': override,
                'families': {family: result},
                'errors': {},
            }
        else:
            report = retention.run_all(dry_run=dry_run, families=families)

        if options['json']:
            self.stdout.write(json.dumps(report, indent=2, default=str))
            return

        mode = 'DRY RUN' if dry_run else 'purged'
        for name, result in report['families'].items():
            detail = ' '.join(f'{key}={value}' for key, value in result.items())
            self.stdout.write(f'{name}: {mode} {detail}')
        for name, error in report['errors'].items():
            self.stderr.write(self.style.WARNING(f'{name}: FAILED {error}'))
        if not report['errors']:
            self.stdout.write(self.style.SUCCESS('retention run complete'))
