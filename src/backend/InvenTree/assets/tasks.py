"""Bounded, fair polling of registered pumphouses through read-only connectors."""

import time
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from assets.ingestion_models import IngestionCheckpoint
from assets.models import AssetMachine
from InvenTree.tasks import ScheduledTask, scheduled_task
from machine_health.connectors.base import (
    PUMPHOUSE_CONNECTOR_TYPES,
    pumphouse_connector_class,
)
from machine_health.connectors.cosmos_pumphouse import CosmosConfigError, _classify
from machine_health.services.ingestion import record_source_error

STATION_BUDGET_SECONDS = 20
RUN_BUDGET_SECONDS = 50
MAX_DOCUMENTS_PER_STATION = 200
LEASE_SECONDS = 120


def connector_for(source, **kwargs):
    """Build the adapter a source declares, resolved from the registry.

    Resolved rather than named: a station may be served by the live Cosmos
    adapter or by the replay one, and the sweep has to drive whichever its own
    source says. Naming one class here would quietly skip every station
    configured for the other.
    """
    connector_class = pumphouse_connector_class(source.connector_type)
    if connector_class is None:
        raise CosmosConfigError('Source names a connector that is not registered.')
    return connector_class(source, **kwargs)


@scheduled_task(ScheduledTask.MINUTES, 1)
def poll_cosmos_pumphouse_sources():
    """Visit least-recently attempted stations first, isolating their failures.

    The conditional database lease prevents overlapping scheduler invocations
    from ingesting the same station. It expires after a crashed worker. Neither
    the network read nor the whole sweep holds a database transaction open.
    """
    if not settings.AIMMS_COSMOS_PUMPHOUSE_ENABLED:
        return 0

    deadline = time.monotonic() + RUN_BUDGET_SECONDS
    checkpoints = (
        IngestionCheckpoint.objects
        .select_related('source', 'station')
        .filter(
            active=True,
            source__active=True,
            source__connector_type__in=PUMPHOUSE_CONNECTOR_TYPES,
        )
        .order_by(F('last_poll_at').asc(nulls_first=True), 'pk')
    )
    attempted = 0
    for checkpoint in checkpoints:
        if time.monotonic() >= deadline:
            break
        now = timezone.now()
        lease_until = now + timedelta(seconds=LEASE_SECONDS)
        with transaction.atomic():
            # Serialize the lease claim with activation/deactivation. Never hold
            # this lock while waiting for the provider.
            if checkpoint.station_id:
                AssetMachine.objects.select_for_update().get(pk=checkpoint.station_id)
            claimed = (
                IngestionCheckpoint.objects
                .filter(pk=checkpoint.pk, active=True)
                .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
                .update(lease_until=lease_until, last_poll_at=now)
            )
        if not claimed:
            continue

        # Refresh after claiming: another sweep may have advanced this row
        # since our initial query. Never resume from that stale position.
        checkpoint.refresh_from_db()
        attempted += 1
        code = ''
        connector = None
        try:
            connector = connector_for(
                checkpoint.source,
                station_uuid=checkpoint.station_uuid,
                deadline=min(deadline, time.monotonic() + STATION_BUDGET_SECONDS),
            )
            connector.ingest(
                checkpoint, max_documents=_document_limit(checkpoint.source)
            )
            code = connector.last_error_code
        except Exception as exc:
            code = _classify(exc)
        finally:
            try:
                if connector is not None:
                    try:
                        connector.close()
                    except Exception as exc:
                        code = code or _classify(exc)
                if code:
                    record_source_error(checkpoint.source, code, checkpoint=checkpoint)
                else:
                    checkpoint.last_success_at = timezone.now()
                    checkpoint.last_error_code = ''
                    checkpoint.save(
                        update_fields=[
                            'last_success_at',
                            'last_error_code',
                            'updated_at',
                        ]
                    )
            finally:
                IngestionCheckpoint.objects.filter(
                    pk=checkpoint.pk, lease_until=lease_until
                ).update(lease_until=None)
    return attempted


def _document_limit(source):
    """Validate account configuration without allowing it to exceed worker limits."""
    try:
        configured = int(
            source.config.get('max_docs_per_poll', MAX_DOCUMENTS_PER_STATION)
        )
    except (TypeError, ValueError) as exc:
        raise CosmosConfigError('Invalid station document budget.') from exc
    if configured <= 0:
        raise CosmosConfigError('Invalid station document budget.')
    return min(configured, MAX_DOCUMENTS_PER_STATION)
