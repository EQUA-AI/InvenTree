"""Bounded TEST history contracts without a database or plant readings."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.db.models import F, Q
from django.test import SimpleTestCase, override_settings

from assets.health_models import AnomalyStatus
from machine_health.api import MachineHealthSignals
from machine_health.serializers import SignalTestAlarmSerializer
from machine_health.services.test_alarms import signal_test_alarms


@override_settings(USE_TZ=True)
class TestAlarmHistoryTest(SimpleTestCase):
    """Check query scope, timestamps and window validation in isolation."""

    def setUp(self):
        """Use fixed synthetic timestamps."""
        self.start = datetime(2026, 10, 27, 8, tzinfo=timezone.utc)
        self.end = self.start + timedelta(hours=1)
        self.machine = object()

    def test_resolved_query_requires_window_overlap_and_exact_machine_binding(self):
        """History is limited to the authorized machine and valid intervals."""
        record = {
            'pk': 10,
            'bindings__pk': 3,
            'metrics': {'binding_id': 3, 'test_value': 159.5, 'unit': 'degC'},
            'first_observed_at': self.start,
            'resolved_at': self.end,
            'status': AnomalyStatus.RESOLVED,
        }
        with patch(
            'machine_health.services.test_alarms.MachineAnomaly.objects'
        ) as objects:
            objects.filter.return_value.values.return_value = [
                record,
                {**record, 'pk': 11, 'bindings__pk': 4},
            ]
            result = signal_test_alarms(self.machine, start=self.start, end=self.end)

        expected = Q(status__in=[AnomalyStatus.OPEN, AnomalyStatus.ACKNOWLEDGED]) | Q(
            Q(resolved_at__gte=F('first_observed_at')),
            status=AnomalyStatus.RESOLVED,
            first_observed_at__lte=self.end,
            resolved_at__gte=self.start,
        )
        args, kwargs = objects.filter.call_args
        self.assertEqual(args, (expected,))
        self.assertEqual(kwargs['machine'], self.machine)
        self.assertEqual(kwargs['bindings__machine'], self.machine)
        self.assertTrue(kwargs['bindings__active'])
        self.assertTrue(kwargs['metrics__synthetic'])
        self.assertEqual(list(result), [3])
        self.assertEqual(result[3][0]['resolved_at'], self.end.isoformat())
        self.assertEqual(result[3][0]['triggered_at'], self.start.isoformat())
        self.assertEqual(
            SignalTestAlarmSerializer(result[3][0]).data['status'], 'resolved'
        )

    def test_default_query_keeps_only_current_exercises(self):
        """Other signal consumers retain their current-only contract."""
        with patch(
            'machine_health.services.test_alarms.MachineAnomaly.objects'
        ) as objects:
            objects.filter.return_value.values.return_value = []
            signal_test_alarms(self.machine)
        self.assertEqual(
            objects.filter.call_args.args[0],
            Q(status__in=[AnomalyStatus.OPEN, AnomalyStatus.ACKNOWLEDGED]),
        )

    def test_api_rejects_partial_inverted_and_excessive_windows(self):
        """Invalid ranges cannot start an unbounded history query."""
        for params in [
            {'from': self.start.isoformat()},
            {'from': self.end.isoformat(), 'to': self.start.isoformat()},
            {
                'from': self.start.isoformat(),
                'to': (self.start + timedelta(days=31)).isoformat(),
            },
            {'from': 'not-a-date', 'to': self.end.isoformat()},
        ]:
            with (
                self.subTest(params=params),
                patch('machine_health.api._machine', return_value=self.machine),
                patch('machine_health.api.signal_rows') as rows,
            ):
                response = MachineHealthSignals().get(
                    SimpleNamespace(query_params=params), 1
                )
                self.assertEqual(response.status_code, 400)
                rows.assert_not_called()

    def test_api_passes_exact_window_and_separate_annotations(self):
        """The API forwards bounds without changing the measured value."""
        params = {'from': self.start.isoformat(), 'to': self.end.isoformat()}
        rows = [{'binding_id': 3, 'value': 40}]
        annotation = {'anomaly_id': 10}
        with (
            patch('machine_health.api._machine', return_value=self.machine),
            patch('machine_health.api.signal_rows', return_value=rows),
            patch(
                'machine_health.services.test_alarms.signal_test_alarms',
                return_value={3: [annotation]},
            ) as tests,
            patch(
                'machine_health.api.MachineSignalSerializer',
                return_value=SimpleNamespace(data=rows),
            ),
            patch('machine_health.api.recorded_telemetry_as_live', return_value=False),
        ):
            response = MachineHealthSignals().get(
                SimpleNamespace(query_params=params), 1
            )
        self.assertEqual(response.status_code, 200)
        tests.assert_called_once_with(self.machine, start=self.start, end=self.end)
        self.assertEqual(response.data['results'][0]['value'], 40)
        self.assertEqual(response.data['results'][0]['test_alarms'], [annotation])
