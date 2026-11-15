"""Synthetic source-authorization controls for recorded history."""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from assets.activation import point_hash
from assets.health_models import HealthSource, MachineSignalBinding
from assets.models import Client, ClientScopeGrant, DictionaryPoint
from assets.registry import ensure_pump, register_station
from machine_health.services.integration_history import (
    HistoryAuthorizationError,
    HistoryPage,
    HistoryReadContext,
    HistorySample,
    HistoryWindow,
    traverse_scoped_history,
)
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='unavailable-in-test',
)
class HistorySourceAuthorityTests(TestCase):
    """Use real local actor and source models, but no real source connection."""

    def setUp(self):
        """Create an active actor and synthetic reviewed original-source mapping."""
        self.actor = get_user_model().objects.create_user(
            username='synthetic-history-actor'
        )
        group = Group.objects.create(name='synthetic history readers')
        self.actor.groups.add(group)
        self.rule, _ = RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.client_record = Client.objects.create(
            name='History synthetic', code='history-synthetic'
        )
        self.foreign_client = Client.objects.create(
            name='History other', code='history-other'
        )
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.client_record
        )
        self.station = register_station(
            client=self.client_record,
            name='History station',
            source_namespace='synthetic',
            source_entity_uuid=uuid4(),
            source_key='HISTORY',
        )
        self.machine = ensure_pump(self.station, 'P1')
        self.source = HealthSource.objects.create(
            name='History source',
            client=self.client_record,
            source_type='scada',
            connector_type='cosmos_pumphouse',
            config={'stations': [str(self.station.source_entity_uuid)]},
        )
        self.point = DictionaryPoint.objects.create(
            station=self.station,
            machine=self.machine,
            path='/synthetic/reading',
            raw_tag='SYNTHETIC',
            display_name='Synthetic reading',
            data_type='number',
            unit='degC',
            unit_status='verified',
            status='approved',
        )
        self.binding = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=self.source,
            dictionary_point=self.point,
            dictionary_hash=point_hash(self.point),
            external_key=self.point.path,
            display_name='Synthetic reading',
            unit='degC',
            active=False,
        )
        self.context = HistoryReadContext(
            environment='synthetic',
            actor_pk=self.actor.pk,
            machine_pk=self.machine.pk,
            binding_pk=self.binding.pk,
            source_pk=self.source.pk,
            generation='synthetic-gen',
        )
        self.window = HistoryWindow(0, 2)

    def make(self):
        """Create the callback for the selected synthetic invocation."""
        from machine_health.services.integration_history_authority import (
            make_history_authorizer,
        )

        return make_history_authorizer(self.actor, self.context, self.window)

    def test_active_actor_native_scope_explicit_grant_and_reviewed_binding_positive(
        self,
    ):
        """A fully admitted current mapping can traverse a fake source."""
        authorize = self.make()
        self.assertTrue(authorize(self.context, self.window))
        calls = []

        def fake(context, window, limit):
            calls.append((context, window, limit))
            return HistoryPage(
                'synthetic-gen', (HistorySample('s1', 0, 5, 'good', 'degC'),), False
            )

        result = traverse_scoped_history(
            self.context,
            self.window,
            read_window=fake,
            authorize=authorize,
            max_samples_per_read=10,
            max_samples_total=10,
            max_reads=3,
        )
        self.assertTrue(result.complete)
        self.assertEqual(len(result.samples), 1)
        self.assertEqual(len(calls), 1)

    def test_authorization_loss_after_read_denies_partial_result(self):
        """A grant removed during the fake read prevents result disclosure."""
        authorize = self.make()
        calls = []

        def fake(context, window, limit):
            calls.append(1)
            self.grant.delete()
            return HistoryPage(
                'synthetic-gen', (HistorySample('s1', 0, 5, 'good', 'degC'),), False
            )

        with self.assertRaises(HistoryAuthorizationError):
            traverse_scoped_history(
                self.context,
                self.window,
                read_window=fake,
                authorize=authorize,
                max_samples_per_read=10,
                max_samples_total=10,
                max_reads=3,
            )
        self.assertEqual(calls, [1])

    def test_denials_are_independent_from_grant_and_native_role(self):
        """Missing grants, native role or active actor are independently denied."""
        from tasks.scope import MaintenanceScope

        fixed_scope = {
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.client_record.pk
            )
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: fixed_scope):
            self.assertTrue(self.make()(self.context, self.window))
            self.grant.delete()
            with self.assertRaises(HistoryAuthorizationError):
                self.make()
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.client_record
        )
        self.rule.can_view = False
        self.rule.save(update_fields=['can_view'])
        with self.assertRaises(HistoryAuthorizationError):
            self.make()
        self.rule.can_view = True
        self.rule.save(update_fields=['can_view'])
        self.assertTrue(self.make()(self.context, self.window))
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
        with self.assertRaises(HistoryAuthorizationError):
            self.make()

    def test_mapping_or_source_changes_invalidate_existing_authorizer(self):
        """Changed mapping or inactive source causes a fresh check to fail."""
        authorize = self.make()
        self.assertTrue(authorize(self.context, self.window))
        MachineSignalBinding.objects.filter(pk=self.binding.pk).update(
            external_key='/synthetic/other'
        )
        self.assertFalse(authorize(self.context, self.window))
        MachineSignalBinding.objects.filter(pk=self.binding.pk).update(
            external_key=self.point.path
        )
        self.assertTrue(authorize(self.context, self.window))
        HealthSource.objects.filter(pk=self.source.pk).update(active=False)
        self.assertFalse(authorize(self.context, self.window))

    def test_context_and_requested_window_cannot_be_swapped(self):
        """Invocation identities and window cannot change after callback creation."""
        authorize = self.make()
        self.assertTrue(authorize(self.context, self.window))
        self.assertFalse(authorize(self.context, HistoryWindow(0, 3)))
        other = HistoryReadContext(
            environment='synthetic',
            actor_pk=self.actor.pk,
            machine_pk=self.machine.pk,
            binding_pk=self.binding.pk,
            source_pk=self.source.pk,
            generation='different',
        )
        self.assertFalse(authorize(other, self.window))
        self.assertFalse(authorize(None, self.window))
        # Even a frozen dataclass can be forcibly mutated by trusted Python
        # code; the callback must compare against detached scalar coordinates.
        object.__setattr__(self.context, 'generation', 'different')
        self.assertFalse(authorize(self.context, self.window))

    def test_wrong_actor_missing_binding_and_wrong_source_fail_closed(self):
        """Wrong actor, unrecognized binding and foreign source are denied."""
        from machine_health.services.integration_history_authority import (
            make_history_authorizer,
        )

        other = get_user_model().objects.create_user(username='synthetic-history-other')
        with self.assertRaises(HistoryAuthorizationError):
            make_history_authorizer(other, self.context, self.window)
        bad = HistoryReadContext(
            environment='synthetic',
            actor_pk=self.actor.pk,
            machine_pk=self.machine.pk,
            binding_pk=self.binding.pk + 9999,
            source_pk=self.source.pk,
            generation='synthetic-gen',
        )
        with self.assertRaises(HistoryAuthorizationError):
            make_history_authorizer(self.actor, bad, self.window)
        HealthSource.objects.filter(pk=self.source.pk).update(
            client=self.foreign_client
        )
        with self.assertRaises(HistoryAuthorizationError):
            self.make()

    def test_native_scope_is_independent_of_explicit_client_grant(self):
        """The grant remains present while a different native scope denies."""
        from tasks.scope import MaintenanceScope

        self.assertTrue(self.make()(self.context, self.window))
        foreign_scope = {
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.foreign_client.pk
            )
        }
        with self.settings(
            AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: foreign_scope
        ):
            self.assertTrue(ClientScopeGrant.objects.filter(pk=self.grant.pk).exists())
            with self.assertRaises(HistoryAuthorizationError):
                self.make()
        self.assertTrue(self.make()(self.context, self.window))

    def test_mapping_approval_and_configured_station_are_rechecked(self):
        """A stale review, changed point meaning or removed station fails closed."""
        authorize = self.make()
        self.assertTrue(authorize(self.context, self.window))
        DictionaryPoint.objects.filter(pk=self.point.pk).update(status='pending')
        self.assertFalse(authorize(self.context, self.window))
        DictionaryPoint.objects.filter(pk=self.point.pk).update(status='approved')
        self.assertTrue(authorize(self.context, self.window))
        DictionaryPoint.objects.filter(pk=self.point.pk).update(data_type='text')
        self.assertFalse(authorize(self.context, self.window))
        DictionaryPoint.objects.filter(pk=self.point.pk).update(data_type='number')
        self.assertTrue(authorize(self.context, self.window))
        HealthSource.objects.filter(pk=self.source.pk).update(config={'stations': []})
        self.assertFalse(authorize(self.context, self.window))

    def test_revoked_role_prevents_dispatch_and_during_read_prevents_output(self):
        """An existing callback rechecks the native role at each boundary."""
        authorize = self.make()
        self.rule.can_view = False
        self.rule.save(update_fields=['can_view'])
        calls = []

        def fake(context, window, limit):
            calls.append(1)
            return HistoryPage('synthetic-gen', (), False)

        with self.assertRaises(HistoryAuthorizationError):
            traverse_scoped_history(
                self.context,
                self.window,
                read_window=fake,
                authorize=authorize,
                max_samples_per_read=10,
                max_samples_total=10,
                max_reads=3,
            )
        self.assertEqual(calls, [])
        self.rule.can_view = True
        self.rule.save(update_fields=['can_view'])

        def revoke(context, window, limit):
            calls.append(1)
            self.rule.can_view = False
            self.rule.save(update_fields=['can_view'])
            return HistoryPage('synthetic-gen', (), False)

        with self.assertRaises(HistoryAuthorizationError):
            traverse_scoped_history(
                self.context,
                self.window,
                read_window=revoke,
                authorize=authorize,
                max_samples_per_read=10,
                max_samples_total=10,
                max_reads=3,
            )
        self.assertEqual(calls, [1])

    def test_superuser_still_requires_the_matching_explicit_client_grant(self):
        """Superuser native authority alone does not grant integration scope."""
        from machine_health.services.integration_history_authority import (
            make_history_authorizer,
        )

        admin = get_user_model().objects.create_superuser(
            username='synthetic-history-admin'
        )
        context = HistoryReadContext(
            environment='synthetic',
            actor_pk=admin.pk,
            machine_pk=self.machine.pk,
            binding_pk=self.binding.pk,
            source_pk=self.source.pk,
            generation='synthetic-gen',
        )
        with self.assertRaises(HistoryAuthorizationError):
            make_history_authorizer(admin, context, self.window)
        ClientScopeGrant.objects.create(user=admin, client=self.client_record)
        self.assertTrue(
            make_history_authorizer(admin, context, self.window)(context, self.window)
        )
