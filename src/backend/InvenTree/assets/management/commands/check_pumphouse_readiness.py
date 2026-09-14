"""Read-only deployment checks, with an optional explicit Cosmos connectivity probe."""

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from assets.activation import configured_stations
from assets.health_models import HealthSource, MachineSignalBinding
from assets.ingestion_models import IngestionCheckpoint
from assets.models import DictionaryPoint
from machine_health.connectors.cosmos_pumphouse import CosmosPumphouseConnector
from machine_health.mimic_layout import layout_coverage


class Command(BaseCommand):
    """Report readiness gaps instead of enabling polling or inferring plant facts."""

    def add_arguments(self, parser):
        """Network access is opt-in; default checks are local only."""
        parser.add_argument('--source', type=int, required=True)
        parser.add_argument('--probe', action='store_true')
        parser.add_argument('--allow-incomplete', action='store_true')

    def handle(self, *args, **options):
        """Return a credential-free checklist and a failing exit status for gaps."""
        try:
            source = HealthSource.objects.get(
                pk=options['source'], connector_type='cosmos_pumphouse'
            )
        except HealthSource.DoesNotExist as exc:
            raise CommandError('No matching Cosmos source.') from exc
        report = self.report(source, options['probe'])
        self.stdout.write(json.dumps(report, indent=2, default=str))
        if not report['ready'] and not options['allow_incomplete']:
            raise CommandError('Deployment checks are incomplete; see report.')

    def report(self, source, probe):
        """Keep connectivity distinct from role verification and plant sign-off."""
        issues = []
        config = source.config if isinstance(source.config, dict) else {}
        for key in ('endpoint', 'database', 'readings_container'):
            if not config.get(key):
                issues.append(f'missing_{key}')
        if not source.client_id or not source.client.active:
            issues.append('client_not_assigned_or_inactive')
        if not source.active:
            issues.append('source_inactive')
        if source.secret_ref and not CosmosPumphouseConnector(source)._is_emulator():
            issues.append('azure_requires_entra_identity')
        checkpoints = list(
            IngestionCheckpoint.objects.filter(
                source=source, active=True
            ).select_related('station')
        )
        configured = set(configured_stations(source))
        linked = {
            checkpoint.station_uuid
            for checkpoint in checkpoints
            if checkpoint.station_id
        }
        if not configured or configured != linked:
            issues.append('station_checkpoint_allowlist_mismatch')
        stations = []
        for checkpoint in checkpoints:
            station = checkpoint.station
            if (
                station is None
                or station.client_id != source.client_id
                or str(station.source_entity_uuid) != checkpoint.station_uuid
            ):
                issues.append('checkpoint_owner_mismatch')
                continue
            coverage = layout_coverage(station)
            bindings = MachineSignalBinding.objects.filter(
                source=source, dictionary_point__station=station, active=True
            )
            pending = (
                DictionaryPoint.objects
                .filter(station=station)
                .exclude(status='approved')
                .count()
            )
            if (
                not station.active
                or not coverage['ready']
                or not bindings.exists()
                or pending
            ):
                issues.append(f'station_{station.pk}_needs_review_or_activation')
            if checkpoint.last_error_code:
                issues.append(f'station_{station.pk}_poll_error')
            stations.append({
                'station': station.pk,
                'active': station.active,
                'approved_bindings': bindings.count(),
                'pending_points': pending,
                'layout': coverage,
                'last_poll_at': checkpoint.last_poll_at,
                'last_error_code': checkpoint.last_error_code,
            })
        connectivity = {'checked': False, 'ok': None, 'code': None}
        if probe:
            connector = CosmosPumphouseConnector(source)
            try:
                ok, code = connector.check()
                connectivity = {'checked': True, 'ok': ok, 'code': code}
                if not ok:
                    issues.append('connectivity_failed')
            finally:
                connector.close()
        return {
            'ready': not issues,
            'production_ready': None,
            'freshness_threshold_seconds': source.freshness_threshold_seconds,
            'source': source.pk,
            'polling_enabled': settings.AIMMS_COSMOS_PUMPHOUSE_ENABLED,
            'issues': issues,
            'stations': stations,
            'connectivity': connectivity,
            'external_checks': [
                'Confirm the deployed identity has Data Reader and no data writer role.',
                'Confirm production measurements, alarm limits, station inventory and sweep RU/latency with the plant.',
            ],
        }
