"""Ingest the tail of each station's history so the tiles have current values.

Only the last few minutes are needed: a tile shows the newest reading per
binding, not a series. Rewinding the cursor to the start of the data would make
the poller walk ten days - hundreds of thousands of documents - to arrive at
exactly the same answer.
"""

import time
from datetime import datetime, timedelta

from assets.health_models import HealthSource
from assets.ingestion_models import IngestionCheckpoint
from assets.models import AssetMachine
from machine_health.connectors.base import pumphouse_connector_class
from machine_health.connectors.cosmos_pumphouse import bucket_of, to_epoch_ms

MINUTES = 3
src = HealthSource.objects.get(pk=1)
ranges = (src.config or {}).get('data_ranges') or {}
for pk, name in ((17, 'Parvathi'), (60, 'Saraswati'), (78, 'Ranganayaka')):
    st = AssetMachine.objects.get(pk=pk)
    uuid = str(st.source_entity_uuid)
    end = datetime.fromisoformat(ranges[uuid]['to'])
    start_ms = to_epoch_ms(end - timedelta(minutes=MINUTES))
    cp = IngestionCheckpoint.objects.get(station=st, active=True)
    IngestionCheckpoint.objects.filter(pk=cp.pk).update(
        sub_time_period=start_ms,
        hour_bucket=str(bucket_of(start_ms)),
        scan_until=None,
        continuation_token='',
        last_error_code='',
        lease_until=None,
    )
    cp.refresh_from_db()
    c = pumphouse_connector_class(src.connector_type)(src, station_uuid=uuid)
    t0 = time.monotonic()
    try:
        docs, readings = c.ingest(cp, now=end, max_documents=200)
        print(
            f'{name:12} ingested {docs} documents / {readings} readings '
            f'in {time.monotonic() - t0:.1f}s'
        )
    except Exception as exc:
        print(f'{name:12} FAILED {type(exc).__name__}: {str(exc)[:90]}')
    finally:
        c.close()
