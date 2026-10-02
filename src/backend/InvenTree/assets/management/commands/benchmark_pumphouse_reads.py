"""Measure bounded source reads without ingesting values or moving checkpoints."""

import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError

from assets.health_models import HealthSource
from assets.ingestion_models import IngestionCheckpoint
from assets.tasks import (
    MAX_DOCUMENTS_PER_STATION,
    RUN_BUDGET_SECONDS,
    STATION_BUDGET_SECONDS,
)
from machine_health.connectors.cosmos_pumphouse import (
    CosmosConfigError,
    CosmosPumphouseConnector,
    _classify,
    to_epoch_ms,
)


class Command(BaseCommand):
    """Explicit network probe with the scheduler's station and sweep limits."""

    def add_arguments(self, parser):
        """Require the configured source; never accept an arbitrary endpoint."""
        parser.add_argument('--source', type=int, required=True)

    def handle(self, *args, **options):
        """Emit duration and query RU charges for the most recent five minutes."""
        try:
            source = HealthSource.objects.get(
                pk=options['source'], active=True, connector_type='cosmos_pumphouse'
            )
        except HealthSource.DoesNotExist as exc:
            raise CommandError('No active Cosmos source.') from exc
        checkpoints = list(
            IngestionCheckpoint.objects
            .filter(
                source=source,
                active=True,
                station__client_id=source.client_id,
                station__active=True,
            )
            .select_related('station')
            .order_by('station_id')
        )
        if not checkpoints or not source.client_id or not source.client.active:
            raise CommandError('No activated stations assigned to this source Client.')
        start = time.monotonic()
        now = datetime.now(timezone.utc)
        results = []
        for checkpoint in checkpoints:
            if time.monotonic() >= start + RUN_BUDGET_SECONDS:
                break
            began = time.monotonic()
            connector = CosmosPumphouseConnector(
                source,
                station_uuid=checkpoint.station_uuid,
                deadline=min(
                    start + RUN_BUDGET_SECONDS, began + STATION_BUDGET_SECONDS
                ),
            )
            documents, code = 0, ''
            scanned = []
            cursor = SimpleNamespace(
                station_uuid=checkpoint.station_uuid,
                sub_time_period=to_epoch_ms(now) - 300001,
                scan_until=None,
            )
            try:
                if (
                    checkpoint.station_uuid not in connector.stations
                    or str(checkpoint.station.source_entity_uuid)
                    != checkpoint.station_uuid
                ):
                    raise CosmosConfigError(
                        'Checkpoint station identity is not configured.'
                    )
                for _ in connector.poll(
                    cursor,
                    now=now,
                    max_documents=min(
                        MAX_DOCUMENTS_PER_STATION,
                        int(
                            source.config.get('max_docs_per_poll')
                            or MAX_DOCUMENTS_PER_STATION
                        ),
                    ),
                    on_scanned=scanned.append,
                ):
                    documents += 1

            except Exception as exc:
                code = _classify(exc)
            finally:
                try:
                    connector.close()
                except Exception as exc:
                    code = code or _classify(exc)
            results.append({
                'station': checkpoint.station_id,
                'documents': documents,
                'window_complete': bool(
                    scanned and scanned[-1] >= to_epoch_ms(now) + 1
                ),
                'elapsed_seconds': round(time.monotonic() - began, 3),
                'query_request_units': connector.request_charge,
                'error_code': code,
            })
        self.stdout.write(
            json.dumps(
                {
                    'read_only': True,
                    'window_seconds': 300,
                    'elapsed_seconds': round(time.monotonic() - start, 3),
                    'stations_attempted': len(results),
                    'stations_total': len(checkpoints),
                    'query_request_units': (
                        sum(row['query_request_units'] for row in results)
                        if all(
                            row['query_request_units'] is not None for row in results
                        )
                        else None
                    ),
                    'results': results,
                },
                indent=2,
            )
        )
        if len(results) != len(checkpoints) or any(
            row['error_code'] or not row['window_complete'] for row in results
        ):
            raise CommandError(
                'Read sweep was incomplete or contained errors; see report.'
            )
