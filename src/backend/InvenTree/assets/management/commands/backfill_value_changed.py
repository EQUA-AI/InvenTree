"""Fill in when each cached signal last reported a different value.

``MachineSignalState.value_changed_at`` is maintained at ingest for free, but a
row cached before the field existed has nothing to fill it from: the cache keeps
one reading, so the database cannot say when that reading last differed. On a
station whose history is a fully consumed recorded window no further document is
ever ingested, so the field would stay null for ever and report nothing - the
same inertness that made migration 0018 necessary for the converter rails.

This walks the recorded window through the connector and sets the field from the
source. It is a command rather than a data migration because it needs the
network, and a migration that cannot run offline is a migration that blocks a
deployment.

Read-only against Cosmos; the only writes are to ``value_changed_at``.
"""

import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from assets.health_models import HealthSource, MachineSignalBinding, MachineSignalState
from assets.models import AssetMachine
from machine_health.connectors.base import pumphouse_connector_class
from machine_health.connectors.cosmos_pumphouse import bucket_of, to_epoch_ms

#: Hours probed across a station's span. The question is "when did this last
#: change", so coverage of the span matters more than density within it: a
#: channel that moves at all will be caught, and one that never moves is the
#: finding.
PROBES = 70


class Command(BaseCommand):
    """Operator-only; reads the recorded window and writes one field."""

    help = 'Backfill when each cached signal last reported a different value'

    def add_arguments(self, parser):
        """Take the source to read through, and an optional preview."""
        parser.add_argument('--source', type=int, required=True)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        """Walk each station's span and record the last change per channel."""
        try:
            source = HealthSource.objects.get(pk=options['source'])
        except HealthSource.DoesNotExist as exc:
            raise CommandError('No such source.') from exc

        ranges = (source.config or {}).get('data_ranges') or {}
        if not ranges:
            raise CommandError(
                'This source has no recorded data_ranges; run discover_data_range '
                'first, or there is no window to walk.'
            )

        connector_class = pumphouse_connector_class(source.connector_type)
        if connector_class is None:
            raise CommandError('Source names a connector that is not registered.')

        wanted = {}
        for binding in MachineSignalBinding.objects.filter(
            active=True, source=source
        ).select_related('dictionary_point__station'):
            point = binding.dictionary_point
            if point:
                wanted.setdefault(point.station_id, {})[point.path] = binding.pk

        changed_at = {}
        for station in AssetMachine.objects.filter(asset_type='pumphouse').order_by(
            'pk'
        ):
            uuid = str(station.source_entity_uuid)
            if uuid not in ranges or station.pk not in wanted:
                continue
            paths = wanted[station.pk]
            start = _instant(ranges[uuid]['from'])
            end = _instant(ranges[uuid]['to'])
            hours = max(1, int((end - start).total_seconds() // 3600))
            stride = max(1, hours // PROBES)
            connector = connector_class(source, station_uuid=uuid)
            seen = {}
            read = 0
            try:
                for index in range(0, hours, stride):
                    moment = start + timedelta(hours=index)
                    document = connector.latest_document(
                        uuid, bucket_of(to_epoch_ms(moment))
                    )
                    if not document:
                        continue
                    read += 1
                    extension = (
                        json.loads(document.get('data1_raw') or '{}').get('dex') or {}
                    )
                    observed = _observed(document)
                    for tag, raw in extension.items():
                        binding_pk = paths.get(f'/dex/{tag}')
                        if binding_pk is None:
                            continue
                        previous = seen.get(binding_pk)
                        if previous is None or previous[0] != raw:
                            # First sight, or a genuine change: this is when the
                            # channel last differed from what came before it.
                            changed_at[binding_pk] = observed
                        seen[binding_pk] = (raw, observed)
            finally:
                connector.close()
            self.stdout.write(
                f'{station.source_key}: {read} documents over {hours} h, '
                f'stride {stride} h'
            )

        self.stdout.write(f'channels with a last-change instant: {len(changed_at)}')

        if options['dry_run']:
            self.stdout.write('Dry run; nothing written.')
            return

        written = 0
        for binding_pk, moment in changed_at.items():
            if moment is None:
                continue
            written += MachineSignalState.objects.filter(
                binding_id=binding_pk, value_changed_at__isnull=True
            ).update(value_changed_at=moment)
        self.stdout.write(f'value_changed_at written on {written} cached states.')


def _instant(text):
    """Parse a recorded range edge."""
    from django.utils import timezone

    return timezone.datetime.fromisoformat(text)


def _observed(document):
    """The plant instant a snapshot describes."""
    from django.utils import timezone

    stamp = document.get('sub_time_period')
    if stamp is None:
        return None
    return timezone.datetime.fromtimestamp(int(stamp) / 1000, tz=timezone.timezone.utc)
