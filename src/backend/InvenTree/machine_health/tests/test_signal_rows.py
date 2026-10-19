"""What each Health signal row says about itself, beyond its value.

R6 P10/P37: a row names the component its reviewed tag dictionary assigns it
to, so the Health table and evidence can say which part a reading belongs to
without guessing from a tag name. No dictionary point, or a point with no
component, is "not recorded" - never the machine as a whole.
"""

from uuid import uuid4

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from assets.health_models import MachineSignalBinding
from assets.registry import ensure_pump, register_station
from assets.registry_models import AssetComponent, DictionaryPoint
from machine_health.serializers import MachineSignalSerializer
from machine_health.services.summary import health_conditions, signal_rows
from part.models import Part

from .fixtures import HealthEnvMixin


class SignalRowComponentTests(HealthEnvMixin, TestCase):
    """The component behind a signal, from the tag dictionary only."""

    def setUp(self):
        """A registered pump with one mapped signal, and a catalogue part."""
        self.build_health_env()
        # A tag dictionary belongs to a registered station's pump.
        self.station = register_station(
            client=self.client_tenant,
            name='Rows station',
            source_namespace='rows-source',
            source_entity_uuid=uuid4(),
            source_key='PH_8',
        )
        self.machine = ensure_pump(self.station, 'P1')
        self.binding.machine = self.machine
        self.binding.save(update_fields=['machine'])
        self.set_signal(3.0)
        self.part = Part.objects.create(
            name='Motor DE bearing', description='Fixture catalogue part'
        )

    def _point(self, tag, component=None):
        return DictionaryPoint.objects.create(
            station=self.station,
            machine=self.machine,
            component=component,
            path=f'/dex/{tag}',
            raw_tag=tag,
            display_name=tag,
        )

    def _component(self, code, status='draft'):
        return AssetComponent.objects.create(
            machine=self.machine,
            part=self.part,
            code=code,
            name='Motor drive-end bearing',
            status=status,
        )

    def test_a_signal_names_the_component_its_dictionary_point_assigns(self):
        """Code, name and whether the assignment is verified come through."""
        component = self._component('PS-MOTOR-DE-BRG:1', status='verified')
        self.binding.dictionary_point = self._point('VIB', component)
        self.binding.save(update_fields=['dictionary_point'])

        [row] = signal_rows(self.machine)

        self.assertEqual(
            row['component'],
            {
                'code': 'PS-MOTOR-DE-BRG:1',
                'name': 'Motor drive-end bearing',
                'status': 'verified',
            },
        )
        self.assertEqual(
            MachineSignalSerializer([row], many=True).data[0]['component'],
            row['component'],
        )

    def test_a_draft_assignment_says_it_is_a_draft(self):
        """An inferred slot is not presented as a verified one."""
        self.binding.dictionary_point = self._point(
            'VIB', self._component('PS-MOTOR-DE-BRG:1')
        )
        self.binding.save(update_fields=['dictionary_point'])

        [row] = signal_rows(self.machine)

        self.assertEqual(row['component']['status'], 'draft')

    def test_an_unattributed_signal_has_no_component(self):
        """No point, or a point with no component: not recorded."""
        [row] = signal_rows(self.machine)
        self.assertIsNone(row['component'])

        self.binding.dictionary_point = self._point('VIB')
        self.binding.save(update_fields=['dictionary_point'])
        [row] = signal_rows(self.machine)
        self.assertIsNone(row['component'])

    def test_another_machines_component_is_not_attributed_here(self):
        """An inconsistent dictionary link cannot borrow another pump's slot."""
        other = ensure_pump(self.station, 'P2')
        component = AssetComponent.objects.create(
            machine=other, part=self.part, code='OTHER:1', name='Other bearing'
        )
        point = self._point('VIB')
        # Simulate an inconsistent pre-existing import, bypassing model clean.
        DictionaryPoint.objects.filter(pk=point.pk).update(component=component)
        self.binding.dictionary_point = point
        self.binding.save(update_fields=['dictionary_point'])
        [row] = signal_rows(self.machine)
        self.assertIsNone(row['component'])

    def test_another_machines_point_is_not_attributed_here(self):
        """A point must also belong to the signal's own machine."""
        point = self._point('VIB', self._component('BRG:1'))
        DictionaryPoint.objects.filter(pk=point.pk).update(
            machine=ensure_pump(self.station, 'P2')
        )
        self.binding.dictionary_point = point
        self.binding.save(update_fields=['dictionary_point'])
        [row] = signal_rows(self.machine)
        self.assertIsNone(row['component'])

    def test_the_component_costs_no_query_per_row(self):
        """One table of signals is still one read, however many rows."""
        component = self._component('PS-MOTOR-DE-BRG:1')
        self.binding.dictionary_point = self._point('VIB', component)
        self.binding.save(update_fields=['dictionary_point'])
        with CaptureQueriesContext(connection) as one:
            signal_rows(self.machine)

        for index in range(3):
            MachineSignalBinding.objects.create(
                machine=self.machine,
                source=self.source,
                external_key=f'extra-{index}',
                display_name=f'Extra {index}',
                dictionary_point=self._point(f'EXTRA{index}', component),
            )
        with CaptureQueriesContext(connection) as four:
            rows = signal_rows(self.machine)

        self.assertEqual(len(rows), 4)
        self.assertEqual(len(four), len(one))
        self.assertTrue(all(row['component'] for row in rows))
        # The batch reader shares the rows and their cost.
        with CaptureQueriesContext(connection) as batch:
            health_conditions([self.machine])
        self.assertLessEqual(len(batch), 3)
