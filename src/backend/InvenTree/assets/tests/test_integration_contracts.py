"""Tests for the internal, source-bound integration contract."""

import importlib
import unittest
from types import SimpleNamespace
from unittest import mock


class MachineIdentityContractTests(unittest.TestCase):
    """Machine identifiers are explicitly namespaced to their source."""

    def test_machine_identity_preserves_native_key_and_source_environment(self):
        """Equal machine PKs in separate environments are not one asset."""
        try:
            contracts = importlib.import_module('assets.integration_contracts')
        except ModuleNotFoundError as exc:
            if exc.name != 'assets.integration_contracts':
                raise
            self.fail('Machine source identity contract has not been implemented')

        first = contracts.MachineSourceIdentity(environment='site-a', pk=42)
        same = contracts.MachineSourceIdentity(environment='site-a', pk=42)
        other = contracts.MachineSourceIdentity(environment='site-b', pk=42)

        self.assertEqual(first.environment, 'site-a')
        self.assertEqual(first.pk, 42)
        self.assertEqual(first, same)
        self.assertNotEqual(first, other)

    def test_machine_identity_rejects_missing_or_invalid_source_coordinates(self):
        """Invalid values must not silently become a usable source key."""
        from assets.integration_contracts import MachineSourceIdentity

        for environment, pk in [
            ('', 1),
            ('   ', 1),
            (None, 1),
            ('site-a', 0),
            ('site-a', -1),
            ('site-a', True),
            ('site-a', '42'),
        ]:
            with self.subTest(environment=environment, pk=pk):
                with self.assertRaises(ValueError):
                    MachineSourceIdentity(environment=environment, pk=pk)


class AttachmentIdentityContractTests(unittest.TestCase):
    """An attachment identity is not its owner's identity or authority."""

    def test_attachment_identity_preserves_source_and_native_key(self):
        """Equal numeric keys across environments and entity kinds remain distinct."""
        integration_contracts = importlib.import_module('assets.integration_contracts')
        identity_type = getattr(integration_contracts, 'AttachmentSourceIdentity', None)
        self.assertIsNotNone(identity_type, 'Attachment identity contract is missing')
        assert identity_type is not None
        identity = identity_type(environment='site-a', pk=42)
        self.assertEqual(identity.environment, 'site-a')
        self.assertEqual(identity.pk, 42)
        self.assertNotEqual(identity, identity_type(environment='site-b', pk=42))
        self.assertNotEqual(
            identity,
            integration_contracts.MachineSourceIdentity(environment='site-a', pk=42),
        )

    def test_attachment_identity_rejects_invalid_coordinates(self):
        """Invalid or coerced coordinates cannot become a source identity."""
        from assets.integration_contracts import AttachmentSourceIdentity

        for environment, pk in [
            ('', 1),
            ('   ', 1),
            (None, 1),
            ('site-a', 0),
            ('site-a', -1),
            ('site-a', True),
            ('site-a', '42'),
        ]:
            with self.subTest(environment=environment, pk=pk):
                with self.assertRaises(ValueError):
                    AttachmentSourceIdentity(environment=environment, pk=pk)


class OtherSourceIdentityTests(unittest.TestCase):
    """Each native record kind has its own qualified identity."""

    def test_source_environments_reject_comparison_spoofing_strings(self):
        """Source coordinates are literal strings, not overridable comparisons."""
        from tasks.integration_sources import WorkSourceIdentity

        from assets.integration_contracts import (
            AttachmentSourceIdentity,
            ClientSourceIdentity,
            LocationSourceIdentity,
            MachineSourceIdentity,
            MaintenanceSourceIdentity,
        )
        from machine_health.integration_contracts import TelemetrySourceIdentity
        from machine_health.services.integration_history import HistoryReadContext

        class SpoofedEnvironment(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        factories = (
            lambda environment: ClientSourceIdentity(environment=environment, pk=1),
            lambda environment: LocationSourceIdentity(environment=environment, pk=1),
            lambda environment: MachineSourceIdentity(environment=environment, pk=1),
            lambda environment: MaintenanceSourceIdentity(
                environment=environment, pk=1
            ),
            lambda environment: AttachmentSourceIdentity(environment=environment, pk=1),
            lambda environment: WorkSourceIdentity(environment=environment, pk=1),
            lambda environment: TelemetrySourceIdentity(
                environment=environment, machine_pk=1, source_pk=2, binding_pk=3
            ),
            lambda environment: HistoryReadContext(
                environment=environment,
                actor_pk=1,
                machine_pk=2,
                binding_pk=3,
                source_pk=4,
                generation='generation-a',
            ),
        )
        for factory in factories:
            with self.subTest(identity=factory):
                self.assertEqual(factory(' site-a ').environment, ' site-a ')
                with self.assertRaises(ValueError):
                    factory(SpoofedEnvironment('site-b'))

    def test_location_identity_requires_distinct_valid_source_coordinates(self):
        """Location identities do not alias machines, environments or invalid IDs."""
        from assets.integration_contracts import (
            LocationSourceIdentity,
            MachineSourceIdentity,
        )

        location = LocationSourceIdentity(environment='site-a', pk=42)
        self.assertNotEqual(
            location, LocationSourceIdentity(environment='site-b', pk=42)
        )
        self.assertNotEqual(
            location, MachineSourceIdentity(environment='site-a', pk=42)
        )
        for environment, pk in (
            ('', 1),
            (' ', 1),
            (None, 1),
            ('site-a', 0),
            ('site-a', -1),
            ('site-a', True),
            ('site-a', '42'),
        ):
            with (
                self.subTest(environment=environment, pk=pk),
                self.assertRaises(ValueError),
            ):
                LocationSourceIdentity(environment=environment, pk=pk)

    def test_work_and_maintenance_identities_reject_invalid_coordinates(self):
        """Do not coerce missing, Boolean or otherwise invalid native keys."""
        from tasks.integration_sources import WorkSourceIdentity

        from assets.integration_contracts import MaintenanceSourceIdentity

        for identity_class in (MaintenanceSourceIdentity, WorkSourceIdentity):
            for environment, pk in [
                ('', 1),
                ('   ', 1),
                (None, 1),
                ('site-a', 0),
                ('site-a', -1),
                ('site-a', True),
                ('site-a', '42'),
            ]:
                with self.subTest(kind=identity_class, environment=environment, pk=pk):
                    with self.assertRaises(ValueError):
                        identity_class(environment=environment, pk=pk)
            self.assertNotEqual(
                identity_class(environment='site-a', pk=42),
                identity_class(environment='site-b', pk=42),
            )


class NativeMachineSourceTests(unittest.TestCase):
    """The source boundary resolves the actor and native scope on every read."""

    def setUp(self):
        """Avoid database access while checking the grant predicate is supplied."""
        grant_patch = mock.patch(
            'assets.integration_authority.explicitly_granted_client_ids',
            return_value=mock.sentinel.granted_clients,
        )
        grant_patch.start()
        self.addCleanup(grant_patch.stop)

    def test_scoped_machine_read_returns_minimal_source_record(self):
        """An authorized machine is identified without returning its model object."""
        try:
            sources = importlib.import_module('assets.integration_sources')
        except ModuleNotFoundError as exc:
            if exc.name != 'assets.integration_sources':
                raise
            self.fail('Native machine source reader has not been implemented')

        actor = SimpleNamespace(pk=12, is_authenticated=True)
        current = SimpleNamespace(pk=12, is_authenticated=True, is_active=True)
        machine = SimpleNamespace(pk=42, name='Synthetic machine', active=True)
        queryset = mock.Mock()
        queryset.filter.return_value.first.return_value = machine
        users = mock.Mock()
        users.objects.filter.return_value.first.return_value = current

        with (
            mock.patch('django.contrib.auth.get_user_model', return_value=users),
            mock.patch(
                'ai.core.integrations.retrieval_authority.fresh_role', return_value=True
            ),
            mock.patch('tasks.scope.scoped_machines', return_value=queryset) as scoped,
        ):
            result = sources.read_machine(actor, environment='site-a', machine_pk=42)

        self.assertEqual(result.identity.environment, 'site-a')
        self.assertEqual(result.identity.pk, 42)
        self.assertEqual(
            getattr(result, 'schema_version', None), 'aimms.machine.basic.v1'
        )
        self.assertEqual(result.name, 'Synthetic machine')
        self.assertTrue(result.active)
        self.assertEqual(
            scoped.call_args_list, [mock.call(current), mock.call(current)]
        )
        self.assertEqual(
            queryset.filter.call_args_list,
            [
                mock.call(pk=42, client_id__in=mock.sentinel.granted_clients),
                mock.call(pk=42, client_id__in=mock.sentinel.granted_clients),
            ],
        )

    def test_cached_role_cannot_override_current_revocation(self):
        """A stale session grant must not authorize a newly revoked reader."""
        from assets.integration_sources import read_machine

        actor = SimpleNamespace(pk=12, is_authenticated=True)
        current = SimpleNamespace(pk=12, is_authenticated=True, is_active=True)
        users = mock.Mock()
        users.objects.filter.return_value.first.return_value = current
        queryset = mock.Mock()
        queryset.filter.return_value.first.return_value = SimpleNamespace(
            pk=42, name='Synthetic machine', active=True
        )
        with (
            mock.patch('django.contrib.auth.get_user_model', return_value=users),
            mock.patch('users.permissions.check_user_role', return_value=True),
            mock.patch(
                'ai.core.integrations.retrieval_authority.fresh_role',
                return_value=False,
            ),
            mock.patch('tasks.scope.scoped_machines', return_value=queryset) as scoped,
        ):
            self.assertIsNone(read_machine(actor, environment='site-a', machine_pk=42))
            scoped.assert_not_called()

    def test_role_revoked_during_read_withholds_the_result(self):
        """Recheck authority after source work and before exposing a record."""
        from assets.integration_sources import read_machine

        actor = SimpleNamespace(pk=12, is_authenticated=True)
        current = SimpleNamespace(pk=12, is_authenticated=True, is_active=True)
        users = mock.Mock()
        users.objects.filter.return_value.first.return_value = current
        queryset = mock.Mock()
        queryset.filter.return_value.first.return_value = SimpleNamespace(
            pk=42, name='Synthetic machine', active=True
        )
        with (
            mock.patch('django.contrib.auth.get_user_model', return_value=users),
            mock.patch(
                'ai.core.integrations.retrieval_authority.fresh_role',
                side_effect=[True, False],
            ),
            mock.patch('tasks.scope.scoped_machines', return_value=queryset),
        ):
            self.assertIsNone(read_machine(actor, environment='site-a', machine_pk=42))

    def test_scope_revoked_during_read_withholds_the_result(self):
        """A source row cannot escape after its client scope is revoked."""
        from assets.integration_sources import read_machine

        actor = SimpleNamespace(pk=12, is_authenticated=True)
        current = SimpleNamespace(pk=12, is_authenticated=True, is_active=True)
        users = mock.Mock()
        users.objects.filter.return_value.first.return_value = current
        queryset = mock.Mock()
        queryset.filter.return_value.first.side_effect = [
            SimpleNamespace(pk=42, name='Synthetic machine', active=True),
            None,
        ]
        with (
            mock.patch('django.contrib.auth.get_user_model', return_value=users),
            mock.patch(
                'ai.core.integrations.retrieval_authority.fresh_role', return_value=True
            ),
            mock.patch('tasks.scope.scoped_machines', return_value=queryset),
        ):
            self.assertIsNone(read_machine(actor, environment='site-a', machine_pk=42))

    def test_missing_actor_is_refused_before_any_lookup(self):
        """An absent or unauthenticated actor cannot trigger a machine read."""
        from assets.integration_sources import read_machine

        with mock.patch('django.contrib.auth.get_user_model') as user_model:
            for actor in (None, SimpleNamespace(pk=12, is_authenticated=False)):
                with self.subTest(actor=actor):
                    self.assertIsNone(
                        read_machine(actor, environment='site-a', machine_pk=42)
                    )
            user_model.assert_not_called()

    def test_malformed_authenticated_actor_id_is_refused_before_lookup(self):
        """Boolean, string and unsaved IDs cannot resolve another user."""
        from assets.integration_sources import read_machine

        with (
            mock.patch('django.contrib.auth.get_user_model') as user_model,
            mock.patch(
                'ai.core.integrations.retrieval_authority.fresh_role',
                return_value=False,
            ),
        ):
            for pk in (None, 0, -1, True, '12'):
                with self.subTest(pk=pk):
                    actor = SimpleNamespace(pk=pk, is_authenticated=True)
                    self.assertIsNone(
                        read_machine(actor, environment='site-a', machine_pk=42)
                    )
            user_model.assert_not_called()

    def test_revoked_role_or_scope_returns_no_record(self):
        """Each read reloads the actor and uses current role and scope."""
        from assets.integration_sources import read_machine

        actor = SimpleNamespace(pk=12, is_authenticated=True)
        current = SimpleNamespace(pk=12, is_authenticated=True, is_active=True)
        users = mock.Mock()
        users.objects.filter.return_value.first.return_value = current
        queryset = mock.Mock()
        queryset.filter.return_value.first.return_value = None

        with (
            mock.patch('django.contrib.auth.get_user_model', return_value=users),
            mock.patch(
                'ai.core.integrations.retrieval_authority.fresh_role',
                side_effect=[False, True],
            ),
            mock.patch('tasks.scope.scoped_machines', return_value=queryset) as scoped,
        ):
            self.assertIsNone(read_machine(actor, environment='site-a', machine_pk=42))
            scoped.assert_not_called()
            self.assertIsNone(read_machine(actor, environment='site-a', machine_pk=42))

        self.assertEqual(users.objects.filter.call_count, 2)
        scoped.assert_called_once_with(current)
        queryset.filter.assert_called_once_with(
            pk=42, client_id__in=mock.sentinel.granted_clients
        )


if __name__ == '__main__':
    unittest.main()
