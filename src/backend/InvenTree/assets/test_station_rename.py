"""Tests for relabelling a registered station.

A station's display name is cosmetic and its source identity is not. These tests
pin that separation from both directions: a rename must not disturb anything
that resolves by identity, and it must not leave a child advertising a name its
parent no longer has.
"""

from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase

from assets.models import AssetMachine, Client
from assets.registry import (
    ensure_pump,
    pump_display_name,
    register_station,
    rename_station,
)

SOURCE_UUID = UUID('bafc976f-1ccc-4a91-aaa6-c3eac2470d36')


class StationRenameTests(TestCase):
    """Relabelling a station and its pump slots."""

    def setUp(self):
        """Register a station with three pump slots."""
        self.client_tenant = Client.objects.create(
            name='Rename Tenant', code=f'rename-{uuid4().hex[:8]}'
        )
        self.station = register_station(
            client=self.client_tenant,
            name='PH_3 Pump Station',
            public_uuid=None,
            source_namespace='klsw',
            source_key='PH_3',
            source_entity_uuid=SOURCE_UUID,
            source_context={},
        )
        for key in ('P1', 'P2', 'P14'):
            ensure_pump(self.station, key)

    def test_the_station_takes_the_new_name(self):
        """The label is what the operator asked for."""
        rename_station(self.station, 'Effluent Pump Station 03')

        self.station.refresh_from_db()
        self.assertEqual(self.station.name, 'Effluent Pump Station 03')

    def test_pump_slots_are_relabelled_too(self):
        """`ensure_pump` writes a child name once, so a rename must revisit them.

        This is the whole reason the helper exists: renaming only the station
        leaves every pump advertising the previous name for good.
        """
        rename_station(self.station, 'Effluent Pump Station 03')

        names = list(self.station.children.values_list('name', flat=True))
        self.assertEqual(
            [n for n in names if n.startswith('PH_3')],
            [],
            'a pump slot kept the old station prefix',
        )
        for name in names:
            self.assertTrue(name.startswith('Effluent Pump Station 03 / Pump '))

    def test_children_match_what_a_fresh_import_would_produce(self):
        """A renamed slot and a newly created one must look identical."""
        rename_station(self.station, 'Effluent Pump Station 03')
        self.station.refresh_from_db()

        pump = self.station.children.get(source_key='P2')
        self.assertEqual(pump.name, pump_display_name(self.station, 'P2'))

    def test_identity_is_untouched(self):
        """Every lookup resolves by identity, so a rename must not move it."""
        before = (
            self.station.uuid,
            self.station.source_entity_uuid,
            self.station.source_key,
            self.station.source_namespace,
        )

        rename_station(self.station, 'Effluent Pump Station 03')
        self.station.refresh_from_db()

        self.assertEqual(
            before,
            (
                self.station.uuid,
                self.station.source_entity_uuid,
                self.station.source_key,
                self.station.source_namespace,
            ),
        )

    def test_pump_uuids_survive_a_rename(self):
        """Pump UUIDs derive from the station UUID, which does not change."""
        before = dict(self.station.children.values_list('source_key', 'uuid'))

        rename_station(self.station, 'Effluent Pump Station 03')

        self.assertEqual(
            before, dict(self.station.children.values_list('source_key', 'uuid'))
        )

    def test_reimport_still_finds_the_station_after_a_rename(self):
        """Registration matches on source identity, never on the label."""
        rename_station(self.station, 'Effluent Pump Station 03')

        again = register_station(
            client=self.client_tenant,
            name='Some Entirely Different Label',
            public_uuid=None,
            source_namespace='klsw',
            source_key='PH_3',
            source_entity_uuid=SOURCE_UUID,
            source_context={},
        )

        self.assertEqual(again.pk, self.station.pk)
        self.assertEqual(AssetMachine.objects.filter(asset_type='pumphouse').count(), 1)

    def test_a_duplicate_name_is_refused(self):
        """Machine names are globally unique; collide loudly, not silently."""
        other = AssetMachine.objects.create(
            name='Effluent Pump Station 03', client=self.client_tenant
        )

        with self.assertRaises(ValidationError):
            rename_station(self.station, other.name)

        self.station.refresh_from_db()
        self.assertEqual(self.station.name, 'PH_3 Pump Station')

    def test_a_blank_name_is_refused(self):
        """An unnamed station is not a rename anyone meant to perform."""
        for candidate in ('', '   ', None):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValidationError):
                    rename_station(self.station, candidate)

    def test_surrounding_whitespace_is_trimmed(self):
        """A stray space would make an otherwise identical name unique."""
        rename_station(self.station, '  Effluent Pump Station 03  ')

        self.station.refresh_from_db()
        self.assertEqual(self.station.name, 'Effluent Pump Station 03')


class RenameCommandTests(TestCase):
    """The operator-facing command."""

    def setUp(self):
        """Register a station with one pump slot."""
        self.client_tenant = Client.objects.create(
            name='Command Tenant', code=f'cmd-{uuid4().hex[:8]}'
        )
        self.station = register_station(
            client=self.client_tenant,
            name='PH_3 Pump Station',
            public_uuid=None,
            source_namespace='klsw',
            source_key='PH_3',
            source_entity_uuid=SOURCE_UUID,
            source_context={},
        )
        ensure_pump(self.station, 'P1')

    def test_it_finds_the_station_by_source_uuid(self):
        """The current name is the thing being replaced, so it is a poor handle."""
        call_command(
            'rename_station',
            source_entity_uuid=str(SOURCE_UUID),
            name='Effluent Pump Station 03',
        )

        self.station.refresh_from_db()
        self.assertEqual(self.station.name, 'Effluent Pump Station 03')

    def test_it_finds_the_station_by_primary_key(self):
        """The alternative handle, for a local operator with the pk to hand."""
        call_command(
            'rename_station', pk=self.station.pk, name='Effluent Pump Station 03'
        )

        self.station.refresh_from_db()
        self.assertEqual(self.station.name, 'Effluent Pump Station 03')

    def test_dry_run_changes_nothing(self):
        """A preview that wrote would be worse than no preview at all."""
        call_command(
            'rename_station',
            pk=self.station.pk,
            name='Effluent Pump Station 03',
            dry_run=True,
        )

        self.station.refresh_from_db()
        self.assertEqual(self.station.name, 'PH_3 Pump Station')
        self.assertTrue(
            self.station.children.get(source_key='P1').name.startswith('PH_3')
        )

    def test_an_unknown_station_is_refused(self):
        """Silently doing nothing would read as success."""
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command(
                'rename_station', source_entity_uuid=str(uuid4()), name='Nowhere'
            )
