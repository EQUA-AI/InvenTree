"""Give newly bound points their current value from the station's newest snapshot.

    python3 contrib/cosmos/devtools/seed_bindings_from_latest.py

Run inside the server container via `manage.py shell <`.

The poller reads strictly after its checkpoint, which is the right rule for a
live source and a dead end for a recorded one: every snapshot up to the
recorded end was accepted long ago, so a binding created today - a point
approved in a later review - has nothing left to receive and stays "No
reading" indefinitely. This reads each activated station's newest snapshot
once and hands the readings for state-less bindings to ``ingest_readings``, the
same door the poller uses, so coercion, the over-range rule and freshness are
the poller's own. Bindings that already hold state are left alone.
"""

from assets.health_models import MachineSignalBinding, MachineSignalState
from assets.ingestion_models import IngestionCheckpoint
from machine_health.connectors.base import pumphouse_connector_class
from machine_health.connectors.cosmos_pumphouse import bucket_of, to_epoch_ms
from machine_health.connectors.pumphouse_payload import flatten_snapshot, in_batches
from machine_health.services.display_time import recorded_range
from machine_health.services.ingestion import ingest_readings

for checkpoint in IngestionCheckpoint.objects.filter(active=True).select_related(
    'source', 'station'
):
    station, source = checkpoint.station, checkpoint.source
    bound = MachineSignalBinding.objects.filter(
        source=source, active=True, dictionary_point__station=station
    )
    empty = set(
        bound.exclude(
            pk__in=MachineSignalState.objects.values_list('binding_id', flat=True)
        ).values_list('external_key', flat=True)
    )
    if not empty:
        print(f'{station.name}: every binding already has a reading')
        continue

    span = recorded_range(station, source)
    connector = pumphouse_connector_class(source.connector_type)(
        source, station_uuid=checkpoint.station_uuid
    )
    try:
        # The newest snapshot the recorded window holds; the checkpoint has
        # already walked to its end, so this is also what the poller last saw.
        end_ms = to_epoch_ms(span[1]) if span else int(checkpoint.sub_time_period)
        document = connector.latest_document(checkpoint.station_uuid, bucket_of(end_ms))
    finally:
        connector.close()
    if document is None:
        print(f'{station.name}: no snapshot in the newest bucket; nothing seeded')
        continue

    readings = [
        reading.as_dict()
        for reading in flatten_snapshot(document)
        if reading.external_key in empty
    ]
    accepted = 0
    for batch in in_batches(readings):
        result = ingest_readings(source, batch, station=station)
        accepted += getattr(result, 'accepted', len(batch))
    print(
        f'{station.name}: {len(empty)} bindings without a reading, '
        f'{len(readings)} present in the newest snapshot, {accepted} ingested'
    )
