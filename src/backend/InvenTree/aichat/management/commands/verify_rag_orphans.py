"""Bounded Search-origin sample and explicit finding recheck; no provider writes."""

import json

from django.core.management.base import BaseCommand, CommandError

from aichat.services.projection_orphans import recheck_orphan, scan_orphans


class Command(BaseCommand):
    """Keep findings and recall stops until exact native/Search proof resolves them."""

    help = 'Inspect Search-origin metadata; --record latches findings. No automatic repair/release.'

    def add_arguments(self, parser):
        """Choose a sample or exact durable finding recheck."""
        parser.add_argument('--corpus', choices=['attachment', 'media'])
        parser.add_argument('--sample', type=int, default=20)
        parser.add_argument('--offset', type=int, default=0)
        parser.add_argument('--record', action='store_true')
        parser.add_argument(
            '--recheck', help='Finding identity; --record required to resolve it'
        )

    def handle(self, *args, **options):
        """No source bodies, credentials or raw provider errors leave this command."""
        try:
            if options['recheck']:
                if not options['record'] or options['corpus']:
                    raise ValueError('Recheck requires --record and no corpus override')
                result = recheck_orphan(identity=options['recheck'])
            else:
                result = scan_orphans(**{
                    key: options[key]
                    for key in ('corpus', 'sample', 'offset', 'record')
                })
        except Exception:
            raise CommandError(
                'Reverse projection audit unavailable; findings remain pending'
            ) from None
        self.stdout.write(json.dumps(result, sort_keys=True))
        if (
            result.get('outcome') in {'incomplete', 'restore_hold', 'drift'}
            or result.get('status') == 'pending'
        ):
            raise CommandError(
                'Reverse projection sample is not clean or finding remains pending'
            )
