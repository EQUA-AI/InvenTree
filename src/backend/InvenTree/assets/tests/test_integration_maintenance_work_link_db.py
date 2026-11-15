"""Synthetic authorization controls for a maintenance-to-Work link."""

from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from tasks.integration_sources import WorkSourceIdentity, read_work_order
from tasks.models import WorkOrder
from tasks.scope import (
    MaintenanceScope,
    maintenance_record_scope_filter,
    scoped_work_orders,
)

from assets.integration_contracts import MaintenanceSourceIdentity
from assets.integration_work_links import (
    MaintenanceWorkLinkSourceRecord,
    read_maintenance_work_link,
)
from assets.models import AssetMachine, AssetMaintenanceRecord, Client, ClientScopeGrant
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from company.models import Company
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='unavailable-in-test',
)
@assume_synthetic_attribution
class MaintenanceWorkLinkTests(TestCase):
    """Both ends require current authority, independently of native flags."""

    def setUp(self):
        """Create one granted actor, a maintenance row and linked Work."""
        self.actor = get_user_model().objects.create_user(username='link-reader')
        group = Group.objects.create(name='Link readers')
        self.actor.groups.add(group)
        self.rule, _ = RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.own = Client.objects.create(name='Synthetic own', code='link-own')
        self.foreign = Client.objects.create(name='Synthetic other', code='link-other')
        self.grant = ClientScopeGrant.objects.create(user=self.actor, client=self.own)
        self.machine = AssetMachine.objects.create(name='Own', client=self.own)
        self.other_machine = AssetMachine.objects.create(
            name='Other', client=self.foreign
        )
        self.work = self.make_work(self.machine)
        self.record = AssetMaintenanceRecord.objects.create(
            machine=self.machine,
            date=date(2020, 1, 2),
            summary='Service',
            work_order=self.work,
        )

    def make_work(self, machine):
        """Create synthetic machine-only Work for a test."""
        return WorkOrder.objects.create(
            title='Synthetic work',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=machine,
        )

    def read(self):
        """Read the selected synthetic maintenance relationship."""
        return read_maintenance_work_link(
            self.actor, environment='synthetic', record_pk=self.record.pk
        )

    def test_authorized_link_and_absent_link(self):
        """Authorized and absent links have different results."""
        link = self.read()
        self.assertEqual(link.schema_version, 'aimms.maintenance.work_link.v1')
        self.assertEqual(link.maintenance.pk, self.record.pk)
        self.assertEqual(link.maintenance.environment, 'synthetic')
        self.assertEqual(link.state, 'available')
        self.assertEqual(link.work.pk, self.work.pk)
        self.assertEqual(link.work.environment, 'synthetic')
        self.record.work_order = None
        self.record.save(update_fields=['work_order'])
        link = self.read()
        self.assertEqual((link.state, link.work), ('none', None))

    def test_deleted_work_is_indistinguishable_from_an_absent_link(self):
        """Characterize SET_NULL; it is not a historical deletion receipt."""
        before = self.read()
        assert before is not None
        self.assertEqual(before.state, 'available')
        self.work.delete()
        current = self.read()
        assert current is not None
        self.assertEqual((current.state, current.work), ('none', None))

    def test_native_scope_and_unrelated_grant_do_not_authorize_target(self):
        """Target visibility alone cannot replace its matching client grant."""
        self.work.machine = self.other_machine
        self.work.save(update_fields=['machine'])
        scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.own.pk),
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.foreign.pk
            ),
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: scopes):
            self.assertTrue(
                scoped_work_orders(self.actor).filter(pk=self.work.pk).exists()
            )
            self.assertIsNone(
                read_work_order(
                    self.actor, environment='synthetic', work_pk=self.work.pk
                )
            )
            for enabled in (True, False):
                with self.settings(AIMMS_WORK_ORDERS_ENABLED=enabled):
                    link = self.read()
                    self.assertEqual((link.state, link.work), ('unknown', None))
            ClientScopeGrant.objects.create(user=self.actor, client=self.foreign)
            self.assertEqual(self.read().state, 'available')

    def test_explicit_source_grant_is_required_with_native_scope_intact(self):
        """A separately configured native maintenance scope is not a client grant."""
        from assets.integration_work_links import _authorized_record

        scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.own.pk)
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: scopes):
            self.assertTrue(
                AssetMaintenanceRecord.objects
                .filter(maintenance_record_scope_filter(self.actor))
                .filter(pk=self.record.pk)
                .exists()
            )
            self.assertIsNotNone(_authorized_record(self.actor.pk, self.record.pk))
            self.assertIsNotNone(self.read())
            self.grant.delete()
            self.assertTrue(
                AssetMaintenanceRecord.objects
                .filter(maintenance_record_scope_filter(self.actor))
                .filter(pk=self.record.pk)
                .exists()
            )
            self.assertIsNone(_authorized_record(self.actor.pk, self.record.pk))
            self.assertIsNone(self.read())
            ClientScopeGrant.objects.create(user=self.actor, client=self.own)
            self.assertIsNotNone(self.read())

    def test_machine_less_shared_work_is_withheld_until_attribution_proven(self):
        """Machine-less shared Work has no proven client attribution."""
        self.work.machine = None
        self.work.save(update_fields=['machine'])
        self.assertTrue(scoped_work_orders(self.actor).filter(pk=self.work.pk).exists())
        link = self.read()
        self.assertEqual((link.state, link.work), ('unknown', None))

    def test_shared_work_on_granted_machine_is_not_attribution_proof(self):
        """Customer-shared Work stays withheld without attribution."""
        customer = Company.objects.create(name='Synthetic customer', is_customer=True)
        self.work.customer = customer
        self.work.save(update_fields=['customer'])
        self.assertTrue(scoped_work_orders(self.actor).filter(pk=self.work.pk).exists())
        self.assertIsNone(
            read_work_order(self.actor, environment='synthetic', work_pk=self.work.pk)
        )
        link = self.read()
        self.assertEqual((link.state, link.work), ('unknown', None))

    def test_denied_source_is_not_an_unknown_or_absent_link(self):
        """A denied maintenance source produces no relationship envelope."""
        self.grant.delete()
        self.assertIsNone(self.read())
        self.grant = ClientScopeGrant.objects.create(user=self.actor, client=self.own)
        self.rule.can_view = False
        self.rule.save(update_fields=['can_view'])
        self.assertIsNone(self.read())

    def test_source_revocation_during_target_read_is_denied(self):
        """A grant revoked during target resolution prevents emission."""

        def revoke(*args, **kwargs):
            result = read_work_order(*args, **kwargs)
            self.grant.delete()
            return result

        with patch('assets.integration_work_links.read_work_order', side_effect=revoke):
            self.assertIsNone(self.read())

    def test_relink_during_target_read_is_not_returned_as_old_edge(self):
        """A relink during resolution cannot return the former Work ID."""
        replacement = self.make_work(self.machine)

        def relink(*args, **kwargs):
            result = read_work_order(*args, **kwargs)
            AssetMaintenanceRecord.objects.filter(pk=self.record.pk).update(
                work_order=replacement
            )
            return result

        with patch('assets.integration_work_links.read_work_order', side_effect=relink):
            self.assertIsNone(self.read())
        self.assertEqual(self.read().work.pk, replacement.pk)

    def test_target_becoming_shared_during_read_is_denied(self):
        """Previously authorized target Work is checked again before emission."""
        customer = Company.objects.create(name='Synthetic customer', is_customer=True)
        calls = []

        def change_target(*args, **kwargs):
            result = read_work_order(*args, **kwargs)
            calls.append(result)
            if len(calls) == 1:
                WorkOrder.objects.filter(pk=self.work.pk).update(customer=customer)
            return result

        with patch(
            'assets.integration_work_links.read_work_order', side_effect=change_target
        ):
            self.assertIsNone(self.read())
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.read().state, 'unknown')

    def test_source_machine_change_during_read_is_denied(self):
        """Even an authorized source reparent cannot return a stale edge."""
        other = AssetMachine.objects.create(name='Another own', client=self.own)

        def reparent(*args, **kwargs):
            result = read_work_order(*args, **kwargs)
            AssetMaintenanceRecord.objects.filter(pk=self.record.pk).update(
                machine=other
            )
            return result

        with patch(
            'assets.integration_work_links.read_work_order', side_effect=reparent
        ):
            self.assertIsNone(self.read())
        self.assertEqual(self.read().state, 'available')

    def test_machine_client_change_during_read_is_denied_even_with_both_grants(self):
        """A stable machine ID does not make an observed client transfer stable."""
        ClientScopeGrant.objects.create(user=self.actor, client=self.foreign)
        self.assertEqual(self.read().state, 'available')
        calls = []

        def transfer(*args, **kwargs):
            result = read_work_order(*args, **kwargs)
            calls.append(result)
            if len(calls) == 1:
                machine = AssetMachine.objects.get(pk=self.machine.pk)
                machine.client = self.foreign
                machine.save(update_fields=['client'])
            return result

        with patch(
            'assets.integration_work_links.read_work_order', side_effect=transfer
        ):
            self.assertIsNone(self.read())
        self.assertEqual(len(calls), 1)

    def test_relink_after_final_target_check_is_denied(self):
        """A mutation after the second Work check reaches the final source check."""
        initial = self.read()
        assert initial is not None
        self.assertEqual(initial.state, 'available')
        calls = []

        def unlink(*args, **kwargs):
            result = read_work_order(*args, **kwargs)
            calls.append(result)
            if len(calls) == 2:
                AssetMaintenanceRecord.objects.filter(pk=self.record.pk).update(
                    work_order=None
                )
            return result

        with patch('assets.integration_work_links.read_work_order', side_effect=unlink):
            self.assertIsNone(self.read())
        self.assertEqual(len(calls), 2)
        current = self.read()
        assert current is not None
        self.assertEqual(current.state, 'none')

    def test_contract_rejects_inconsistent_states_and_cross_source_identity(self):
        """Invalid envelopes cannot confuse absent, withheld or available links."""

        class PretendsEqual:
            __hash__ = None

            def __eq__(self, other):
                return True

        maintenance = MaintenanceSourceIdentity(environment='synthetic', pk=1)
        work = WorkSourceIdentity(environment='synthetic', pk=1)
        foreign = WorkSourceIdentity(environment='another-source', pk=1)
        for state, value in (
            ('none', work),
            ('unknown', work),
            ('available', None),
            ('available', foreign),
            ('invalid', None),
            (PretendsEqual(), work),
        ):
            with self.subTest(state=state, value=value), self.assertRaises(ValueError):
                MaintenanceWorkLinkSourceRecord(maintenance, state, value)
