"""R6 UI01: the Machines list shows each machine's Health condition and coverage."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.db.models import Q
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tasks.scope import ScopeError

from assets.health_models import (
    HealthSource,
    MachineSignalBinding,
    MachineSignalState,
    SignalQuality,
    SourceType,
)
from assets.models import AssetMachine, Client, ClientScopeGrant
from machine_health.services.summary import health_summary
from users.models import RuleSet

URL = '/api/assets/locations/machines/'


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class LocationConditionTests(TestCase):
    """Normal reader, stored role and native client grant; real Health rows."""

    def setUp(self):
        """A mapped pump, a stale one and one with nothing mapped."""
        self.reader = get_user_model().objects.create_user(username='condition')
        group = Group.objects.create(name='Condition readers')
        self.reader.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.tenant = Client.objects.create(name='Condition client', code='cond')
        ClientScopeGrant.objects.create(user=self.reader, client=self.tenant)
        self.source = HealthSource.objects.create(
            name='SCADA condition',
            source_type=SourceType.SCADA,
            connector_type='webhook',
            secret_ref='health/condition',
            freshness_threshold_seconds=900,
        )
        self.mapped = self.machine('Mapped pump', 5.0, age=30)
        self.stale = self.machine('Stale pump', 3.0, age=30, stale_value=8.0)
        self.unmapped = AssetMachine.objects.create(
            name='Unmapped valve', client=self.tenant
        )
        self.client.force_login(self.reader)

    def machine(self, name, value, *, age, stale_value=None):
        """A machine with one current reading and optionally a stale one."""
        machine = AssetMachine.objects.create(name=name, client=self.tenant)
        readings = [(value, age)]
        if stale_value is not None:
            readings.append((stale_value, 4000))
        for index, (reading, seconds) in enumerate(readings):
            binding = MachineSignalBinding.objects.create(
                machine=machine,
                source=self.source,
                external_key=f'{name}.{index}',
                display_name=f'{name} {index}',
                signal_kind='vibration',
                unit='mm/s',
                normal_max=4.5,
                warn_max=6.0,
                critical_max=9.0,
            )
            observed = timezone.now() - timedelta(seconds=seconds)
            MachineSignalState.objects.create(
                binding=binding,
                value={'value': reading, 'unit': 'mm/s'},
                observed_at=observed,
                received_at=observed,
                quality=SignalQuality.GOOD,
            )
        return machine

    def rows(self, **params):
        """Rows by name, as the Machines list reads them."""
        response = self.client.get(URL, params)
        self.assertEqual(response.status_code, 200, response.content)
        return {row['name']: row for row in response.json()['results']}

    def test_condition_is_the_health_tabs_own(self):
        """Each row carries the Health tab's state and coverage, nothing more."""
        rows = self.rows()
        for machine in (self.mapped, self.stale, self.unmapped):
            with self.subTest(machine=machine.name):
                summary = health_summary(machine)
                self.assertEqual(
                    rows[machine.name]['condition'],
                    {
                        key: summary[key]
                        for key in (
                            'state',
                            'configured',
                            'signal_count',
                            'stale_signal_count',
                            'degraded_data',
                        )
                    },
                )
        self.assertEqual(rows['Mapped pump']['condition']['state'], 'warning')
        self.assertEqual(rows['Stale pump']['condition']['stale_signal_count'], 1)
        self.assertEqual(
            rows['Unmapped valve']['condition'],
            {
                'state': 'unknown',
                'configured': False,
                'signal_count': 0,
                'stale_signal_count': 0,
                'degraded_data': False,
            },
        )

    def test_unresolved_health_scope_is_unknown_not_normal(self):
        """Without a Health scope every row says it could not be judged."""
        with mock.patch(
            'assets.location_api.machine_scope_filter',
            side_effect=ScopeError('unavailable'),
        ):
            rows = self.rows()
        self.assertEqual({row['condition'] for row in rows.values()}, {None})

    def test_fresh_unbounded_machine_is_unknown_on_the_index(self):
        """Usable readings after activation do not imply an assessed condition."""
        MachineSignalBinding.objects.filter(machine=self.mapped).update(
            normal_max=None, warn_max=None, critical_max=None
        )
        condition = self.rows()['Mapped pump']['condition']
        self.assertEqual(condition['state'], 'unknown')
        self.assertFalse(condition['degraded_data'])
        self.assertEqual(condition['stale_signal_count'], 0)

    def test_rows_outside_the_health_scope_get_no_condition(self):
        """The list never judges a machine its Health tab would refuse."""
        with mock.patch(
            'assets.location_api.machine_scope_filter',
            return_value=Q(pk=self.mapped.pk),
        ):
            rows = self.rows()
        self.assertIsNotNone(rows['Mapped pump']['condition'])
        self.assertIsNone(rows['Stale pump']['condition'])
        self.assertIsNone(rows['Unmapped valve']['condition'])

    def test_conditions_cost_the_same_queries_for_a_longer_page(self):
        """More mapped machines add no per-row Health queries."""
        self.rows()
        with CaptureQueriesContext(connection) as three:
            self.rows()
        for index in range(4):
            self.machine(f'Extra pump {index}', 3.0, age=30)
        with CaptureQueriesContext(connection) as seven:
            rows = self.rows()
        self.assertEqual(len(rows), 7)
        self.assertEqual(len(seven), len(three))
