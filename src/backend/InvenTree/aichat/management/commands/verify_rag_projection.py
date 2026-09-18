"""Read-only metadata sample with optional count-only evidence and stop recording."""

import json

from django.core.management.base import BaseCommand, CommandError

from aichat.services.projection_audit import verify_projection


class Command(BaseCommand):
    """No projection repair or gate release is implicit in an audit."""

    help = 'Sample Search projection metadata. --record persists evidence and critical recall stops.'

    def add_arguments(self, parser):
        """Explicit corpus, bounded sample and keyset continuation."""
        parser.add_argument('--corpus', choices=['attachment', 'media'], required=True)
        parser.add_argument('--sample', type=int, default=20)
        parser.add_argument('--after', type=int, default=0)
        parser.add_argument('--record', action='store_true')
        parser.add_argument('--fail-on-incomplete', action='store_true')

    def handle(self, *args, **options):
        """Only aggregate counts leave this command, never provider errors or text."""
        try:
            result = verify_projection(**{
                name: options[name] for name in ('corpus', 'sample', 'after', 'record')
            })
        except Exception:
            raise CommandError('Projection audit unavailable') from None
        self.stdout.write(json.dumps(result, sort_keys=True))
        if options['fail_on_incomplete'] and result['outcome'] != 'clean_sample':
            raise CommandError('Projection sample is not clean and complete')
