"""Export only client-labelled durable facts to a new private operator artifact."""

import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from aichat.models import MemoryFact
from assets.models import Client


class Command(BaseCommand):
    """No transcripts or vectors; full offboarding/DSAR coverage is not implied."""

    help = 'Export a client durable-memory snapshot projection to a new mode-0600 JSON file.'

    def add_arguments(self, parser):
        """Operator identity is supplied through the trusted command environment."""
        parser.add_argument('--client-id', type=int, required=True)
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        """Remove any incomplete artifact; never send content to stdout."""
        client = Client.objects.filter(pk=options['client_id']).first()
        if client is None:
            raise CommandError('Unknown client')
        cutoff = timezone.now()
        target = Path(options['output'])
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            raise CommandError('Cannot create a new private export') from None
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                stream.write(
                    '{"schema_version":1,"scope":"client_durable_facts","consistency":"live_read_creation_cutoff","records":['
                )
                after, count, separator = None, 0, ''
                while True:
                    rows = MemoryFact.objects.filter(
                        client_code=client.code, created_at__lte=cutoff
                    ).order_by('pk')
                    if after is not None:
                        rows = rows.filter(pk__gt=after)
                    page = list(
                        rows.values(
                            'id',
                            'owner_id',
                            'client_code',
                            'entity_kind',
                            'entity_id',
                            'memory_type',
                            'topics',
                            'text',
                            'text_lang',
                            'canonical_value',
                            'canonical_unit',
                            'verification_class',
                            'lifecycle_state',
                            'version',
                            'created_at',
                        )[:200]
                    )
                    if not page:
                        break
                    for row in page:
                        stream.write(separator)
                        json.dump(row, stream, cls=DjangoJSONEncoder)
                        separator = ',\n'
                        count += 1
                    after = page[-1]['id']
                stream.write('],"complete":true,"count":' + str(count) + '}\n')
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise CommandError('Export failed; incomplete artifact removed') from None
        self.stdout.write(
            'Client durable-memory export complete; other stores are excluded.'
        )
