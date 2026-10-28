"""Original Cosmos history stays scoped, truthful, and separate from replay."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.http import Http404
from django.test import TestCase, override_settings

from assets.activation import point_hash
from assets.health_models import HealthSource, MachineSignalBinding, MachineSignalState
from assets.models import Client, DictionaryPoint
from assets.registry import ensure_pump, register_station
from machine_health.api import (
    MachineHealthDataRange,
    MachineHealthSeries,
    MachineHealthSignals,
)
from machine_health.connectors.base import get_connector
from machine_health.connectors.cosmos_pumphouse import (
    CosmosPumphouseConnector,
    to_epoch_ms,
)
from machine_health.services.historical import (
    get_historical_connector,
    historical_bindings,
    resolve_historical_source,
)
from machine_health.services.series import SeriesError, read_series, series_scope

from .test_cosmos_pumphouse import FakeContainer, snapshot


@override_settings(USE_TZ=True, AIMMS_RECORDED_TELEMETRY_AS_LIVE=True)
class HistoricalSourceTest(TestCase):
    """Use the native models and connector with an isolated fake SDK container."""

    @classmethod
    def setUpTestData(cls):
        """Retain inactive original mappings beside an active replay source."""
        cls.tenant = Client.objects.create(
            name='Historical client', code='history-test'
        )
        cls.station = register_station(
            client=cls.tenant,
            name='Historical station',
            source_namespace='history',
            source_entity_uuid=uuid4(),
            source_key='PH_HISTORY',
        )
        cls.pump = ensure_pump(cls.station, 'P8')
        cls.sibling = ensure_pump(cls.station, 'P9')
        cls.end = datetime(2025, 8, 1, 2, 59, 55, 99000, tzinfo=timezone.utc)
        cls.start = cls.end - timedelta(hours=6)
        cls.source = HealthSource.objects.create(
            name='Original Cosmos',
            client=cls.tenant,
            source_type='scada',
            connector_type='cosmos_pumphouse',
            config={
                'stations': [str(cls.station.source_entity_uuid)],
                'data_ranges': {
                    str(cls.station.source_entity_uuid): {
                        'from': (cls.end - timedelta(days=30)).isoformat(),
                        'to': cls.end.isoformat(),
                    }
                },
            },
        )
        cls.point = DictionaryPoint.objects.create(
            station=cls.station,
            machine=cls.pump,
            path='/dex/PUMP8_WINDING5',
            raw_tag='PUMP8_WINDING5',
            display_name='Winding 5',
            data_type='number',
            unit='degC',
            unit_status='verified',
            status='approved',
            review_note='Test review',
        )
        cls.original = MachineSignalBinding.objects.create(
            machine=cls.pump,
            source=cls.source,
            dictionary_point=cls.point,
            dictionary_hash=point_hash(cls.point),
            external_key=cls.point.path,
            display_name=cls.point.display_name,
            unit=cls.point.unit,
            active=False,
            critical_max=120,
        )
        cls.original_state = MachineSignalState.objects.create(
            binding=cls.original,
            value={'value': 199},
            observed_at=cls.end,
            quality='good',
        )
        cls.replay = HealthSource.objects.create(
            name='TEST REPLAY',
            client=cls.tenant,
            source_type='scada',
            connector_type='webhook',
        )
        cls.replay_binding = MachineSignalBinding.objects.create(
            machine=cls.pump,
            source=cls.replay,
            external_key=cls.point.path,
            display_name='Replay winding',
            unit='degC',
            active=True,
        )
        MachineSignalState.objects.create(
            binding=cls.replay_binding,
            value={'value': 96},
            observed_at=cls.end,
            quality='good',
        )

    def document(self, stamp=None, value=43.6):
        """Build a source snapshot using the production payload normalizer."""
        return snapshot(
            to_epoch_ms(stamp or self.end),
            station=str(self.station.source_entity_uuid),
            dex={'PUMP8_WINDING5': value, 'PRIVATE_UNMAPPED': 999},
        )

    def request(self, view, **params):
        """Keep the native authorized parent lookup explicit in API checks."""
        with patch('machine_health.api._machine', return_value=self.pump):
            return view().get(SimpleNamespace(query_params=params), self.pump.pk)

    def test_signals_reads_real_snapshot_and_never_cached_or_replay_values(self):
        """The source's value, date and original id survive without any save."""
        container = FakeContainer([self.document()])
        with (
            patch.object(CosmosPumphouseConnector, 'container', return_value=container),
            patch('machine_health.services.test_alarms.signal_test_alarms') as alarms,
        ):
            response = self.request(
                MachineHealthSignals, history_source=str(self.source.pk)
            )
        [row] = response.data['results']
        self.assertEqual(row['value'], 43.6)
        self.assertEqual(row['binding_id'], self.original.pk)
        self.assertEqual(row['source_id'], self.source.pk)
        self.assertFalse(row['active'])
        self.assertFalse(row['stale'])
        self.assertEqual(row['state'], 'normal')
        self.assertEqual(row['test_alarms'], [])
        self.assertIsNone(row['received_at'])
        self.assertEqual(response.data['snapshot_at'], self.end.isoformat())
        self.assertEqual(response.data['telemetry_presentation'], 'recorded_history')
        self.assertFalse(response.data['recorded_telemetry_as_live'])
        self.assertFalse(response.data['display_shifted'])
        alarms.assert_not_called()
        self.original.refresh_from_db()
        self.original_state.refresh_from_db()
        self.assertFalse(self.original.active)
        self.assertEqual(self.original_state.value['value'], 199)
        self.assertEqual(len(container.calls), 1)
        self.assertFalse(container.calls[0]['enable_cross_partition_query'])
        self.assertEqual(
            container.calls[0]['partition_key'][0], str(self.station.source_entity_uuid)
        )

    def test_series_samples_all_six_source_hours_with_original_ids(self):
        """An opt-in read keeps real timestamps even when live relabeling is on."""
        docs = [
            self.document(self.start + timedelta(seconds=120 * i), value=40 + i / 100)
            for i in range(180)
        ]
        container = FakeContainer(docs)
        with patch.object(
            CosmosPumphouseConnector, 'container', return_value=container
        ):
            data = read_series(
                self.pump,
                binding_ids=[self.original.pk, self.replay_binding.pk],
                start=self.start,
                end=self.end,
                points=180,
                historical_source=self.source,
            )
        [series] = data['series']
        self.assertEqual(series['binding_id'], self.original.pk)
        self.assertEqual(series['count'], 180)
        self.assertEqual(data['documents_read'], 180)
        self.assertEqual(series['samples'][0]['t'], to_epoch_ms(self.start))
        self.assertEqual(series['samples'][-1]['v'], 41.79)
        self.assertEqual(data['window_end'], self.end.isoformat())
        self.assertEqual(data['source_to'], self.end.isoformat())
        self.assertEqual(data['display_shift_seconds'], 0)
        self.assertEqual(data['telemetry_presentation'], 'recorded_history')
        self.assertFalse(data['recorded_telemetry_as_live'])
        self.assertFalse(data['live']['enabled'])
        self.assertTrue(
            all(not call['enable_cross_partition_query'] for call in container.calls)
        )
        self.assertTrue(all('TOP 1' in call['query'] for call in container.calls))

    def test_data_range_is_source_clock_and_default_series_ends_there(self):
        """The range and omitted series end never substitute the wall clock."""
        response = self.request(
            MachineHealthDataRange, history_source=str(self.source.pk)
        )
        self.assertEqual(response.data['to'], self.end.isoformat())
        self.assertEqual(response.data['to'], response.data['source_to'])
        self.assertEqual(response.data['from'], response.data['source_from'])
        self.assertEqual(response.data['source_id'], self.source.pk)
        with patch.object(
            CosmosPumphouseConnector, 'container', return_value=FakeContainer()
        ):
            data = read_series(
                self.pump, binding_ids=[self.original.pk], historical_source=self.source
            )
        self.assertEqual(data['window_end'], self.end.isoformat())

    def test_default_scope_and_checkpoint_guard_stay_unchanged(self):
        """Inactive original mappings are invisible without explicit history mode."""
        self.assertEqual(list(series_scope(self.pump)), [self.replay_binding])
        self.assertIsNone(get_connector(self.source, machine=self.pump))
        connector = get_historical_connector(self.source, machine=self.pump)
        self.assertIsInstance(connector, CosmosPumphouseConnector)
        self.assertEqual(connector.station, str(self.station.source_entity_uuid))
        with patch(
            'machine_health.services.test_alarms.signal_test_alarms', return_value={}
        ) as alarms:
            response = self.request(MachineHealthSignals)
        [row] = response.data['results']
        self.assertEqual(row['binding_id'], self.replay_binding.pk)
        self.assertEqual(row['value'], 96)
        alarms.assert_called_once()

    def test_source_is_404_for_foreign_client_disabled_wrong_station_or_connector(self):
        """A source identifier never grants source or tenant access."""
        other = Client.objects.create(
            name='Other historical client', code='other-history'
        )
        variants = [
            {'client': other},
            {'active': False},
            {'config': {'stations': [str(uuid4())]}},
            {'connector_type': 'cosmos_pumphouse_replay'},
        ]
        for index, changes in enumerate(variants):
            source = HealthSource.objects.create(
                **{
                    'name': f'Invalid source {index}',
                    'client': self.tenant,
                    'source_type': 'scada',
                    'connector_type': 'cosmos_pumphouse',
                    'config': self.source.config,
                    **changes,
                }
            )
            with self.subTest(changes=list(changes)), self.assertRaises(Http404):
                resolve_historical_source(self.pump, source.pk)

    def test_missing_unreviewed_or_changed_mapping_is_404(self):
        """Revoked or altered dictionary meaning cannot revive an old binding."""
        for field, invalid, original in [
            ('status', 'rejected', 'approved'),
            ('path', '/private', self.point.path),
            ('unit_status', 'unresolved', 'verified'),
        ]:
            DictionaryPoint.objects.filter(pk=self.point.pk).update(**{field: invalid})
            with self.subTest(field=field), self.assertRaises(Http404):
                resolve_historical_source(self.pump, self.source.pk)
            DictionaryPoint.objects.filter(pk=self.point.pk).update(**{field: original})
        MachineSignalBinding.objects.filter(pk=self.original.pk).update(
            dictionary_hash='changed'
        )
        with self.assertRaises(Http404):
            resolve_historical_source(self.pump, self.source.pk)

    def test_sibling_and_private_tags_never_reach_the_connector(self):
        """A pump cannot select another pump's mapping or a free-form tag."""
        point = DictionaryPoint.objects.create(
            station=self.station,
            machine=self.sibling,
            path='/dex/PRIVATE_SIBLING',
            raw_tag='PRIVATE_SIBLING',
            display_name='Sibling',
            data_type='number',
            unit='degC',
            unit_status='verified',
            status='approved',
        )
        sibling = MachineSignalBinding.objects.create(
            machine=self.sibling,
            source=self.source,
            dictionary_point=point,
            dictionary_hash=point_hash(point),
            external_key=point.path,
            display_name='Sibling',
            unit='degC',
            active=False,
        )
        with patch.object(CosmosPumphouseConnector, 'container') as container:
            data = read_series(
                self.pump,
                binding_ids=[sibling.pk],
                keys=['/dex/PRIVATE_UNMAPPED'],
                start=self.start,
                end=self.end,
                historical_source=self.source,
            )
        self.assertEqual(data['series'], [])
        container.assert_not_called()
        self.assertEqual(
            [b.pk for b in historical_bindings(self.pump, self.source)],
            [self.original.pk],
        )

    def test_source_errors_return_unknown_without_cache_fallback_or_provider_text(self):
        """A failing provider cannot reveal its exception or reuse test values."""
        container = FakeContainer(
            error=RuntimeError('private endpoint credential text')
        )
        with patch.object(
            CosmosPumphouseConnector, 'container', return_value=container
        ):
            response = self.request(
                MachineHealthSignals, history_source=str(self.source.pk)
            )
        [row] = response.data['results']
        self.assertIsNone(row['value'])
        self.assertEqual(row['state'], 'unknown')
        self.assertEqual(response.data['read_error_code'], 'NETWORK')
        self.assertNotIn('private', str(response.data))

    def test_window_bounds_are_checked_before_source_reads(self):
        """Live month policy does not widen the explicit historical read budget."""
        with patch.object(CosmosPumphouseConnector, 'container') as container:
            for start, end, code in [
                (self.end - timedelta(hours=25), self.end, 'WINDOW_TOO_LONG'),
                (self.start, self.end + timedelta(seconds=1), 'WINDOW_OUT_OF_RANGE'),
            ]:
                with self.subTest(code=code), self.assertRaises(SeriesError) as caught:
                    read_series(
                        self.pump,
                        binding_ids=[self.original.pk],
                        start=start,
                        end=end,
                        historical_source=self.source,
                    )
                self.assertEqual(caught.exception.code, code)
        container.assert_not_called()

    def test_every_api_authorizes_parent_before_source_resolution(self):
        """Denied machine scope stops even a valid-looking historical selector."""
        for view in [MachineHealthSignals, MachineHealthSeries, MachineHealthDataRange]:
            with (
                self.subTest(view=view.__name__),
                patch('machine_health.api._machine', side_effect=Http404),
                patch('machine_health.api.resolve_historical_source') as source,
                self.assertRaises(Http404),
            ):
                view().get(
                    SimpleNamespace(
                        query_params={'history_source': str(self.source.pk)}
                    ),
                    self.pump.pk,
                )
            source.assert_not_called()
