"""Scope codec, adapter, and scanner-principal tests (SC-RR-004 basis)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from tasks.scope import MaintenanceScope

from .risk_scope import (
    RiskScopeError,
    authorized_scope_keys,
    decode_scope_key,
    encode_scope,
    get_source_adapter,
    risk_service_user,
)
from .risk_testing import RISK_FLAGS, RiskEnvMixin


class ScopeCodecTest(TestCase):
    """The codec is the single reversible scope-key encoding."""

    def test_round_trip_customer_only(self):
        """Customer-only scopes encode and decode losslessly."""
        scope = MaintenanceScope(customer_id=42, site_key=None)
        self.assertEqual(decode_scope_key(encode_scope(scope)), scope)
        self.assertEqual(encode_scope(scope), 'c42')

    def test_round_trip_with_site(self):
        """Site-qualified scopes encode and decode losslessly."""
        scope = MaintenanceScope(customer_id=7, site_key='plant-3')
        key = encode_scope(scope)
        self.assertEqual(key, 'c7~plant-3')
        self.assertEqual(decode_scope_key(key), scope)

    def test_round_trip_client_only(self):
        """Client scopes encode as ``k<id>`` and decode losslessly."""
        scope = MaintenanceScope(customer_id=None, site_key=None, client_id=9)
        self.assertEqual(encode_scope(scope), 'k9')
        self.assertEqual(decode_scope_key('k9'), scope)

    def test_round_trip_client_with_site(self):
        """Site-qualified client scopes encode and decode losslessly."""
        scope = MaintenanceScope(customer_id=None, site_key='plant-3', client_id=7)
        key = encode_scope(scope)
        self.assertEqual(key, 'k7~plant-3')
        self.assertEqual(decode_scope_key(key), scope)

    def test_rejects_untrusted_strings(self):
        """Arbitrary caller strings are never trusted."""
        for bad in (
            '',
            'c0',
            'k0',
            'x1',
            '1',
            'c-1',
            'k-1',
            'c1~',
            'c1~bad key',
            'c1~' + 'a' * 70,
        ):
            with self.assertRaises(RiskScopeError):
                decode_scope_key(bad)

    def test_rejects_unencodable_scopes(self):
        """Unresolved or unencodable scopes fail closed."""
        with self.assertRaises(RiskScopeError):
            encode_scope(MaintenanceScope(customer_id=None, site_key=None))
        with self.assertRaises(RiskScopeError):
            encode_scope(MaintenanceScope(customer_id=1, site_key='bad key'))
        with self.assertRaises(RiskScopeError):
            encode_scope(
                MaintenanceScope(customer_id=None, site_key='bad key', client_id=1)
            )


@override_settings(**RISK_FLAGS)
class ScopeAdapterTest(RiskEnvMixin, TestCase):
    """Source adapters prove membership before any aggregation."""

    def setUp(self):
        """Build the two-customer, two-client environment."""
        self.build_env()
        self.addCleanup(self.teardown_scopes)

    def make_client_work_order(self, machine):
        """Create a customer-NULL work order owned via its machine's client."""
        from tasks.models import WorkOrder

        return WorkOrder.objects.create(
            title='WO',
            status='backlog',
            priority='medium',
            lifecycle_status='ready',
            customer=None,
            machine=machine,
        )

    def test_authorized_scope_keys_sorted(self):
        """Customer and client keys are enumerated deterministically."""
        self.assertEqual(
            authorized_scope_keys(self.actor), [self.scope_key, self.client_scope_key]
        )

    def test_unknown_adapter_aborts(self):
        """A missing adapter aborts instead of returning empty."""
        with self.assertRaises(RiskScopeError):
            get_source_adapter('nonexistent_source')

    def test_site_scope_aborts(self):
        """No source can prove site-level membership today."""
        adapter = get_source_adapter('work_order')
        with self.assertRaises(RiskScopeError):
            adapter.queryset_for_scope(
                actor=self.actor,
                scope=MaintenanceScope(customer_id=self.customer.pk, site_key='s1'),
            )

    def test_work_order_adapter_membership(self):
        """Customer scopes see explicit-customer rows; nothing via machines."""
        mine = self.make_work_order()
        mine_via_machine = self.make_client_work_order(self.machine)
        theirs = self.make_work_order(customer=self.other_customer)
        foreign_via_machine = self.make_client_work_order(self.other_machine)
        queryset = get_source_adapter('work_order').queryset_for_scope(
            actor=self.actor, scope=self.scope
        )
        ids = set(queryset.values_list('pk', flat=True))
        self.assertIn(mine.pk, ids)
        self.assertNotIn(mine_via_machine.pk, ids)
        self.assertNotIn(theirs.pk, ids)
        self.assertNotIn(foreign_via_machine.pk, ids)

    def test_work_order_adapter_client_membership(self):
        """Client scopes see customer-NULL rows on their machines only."""
        explicit_customer = self.make_work_order()
        mine_via_machine = self.make_client_work_order(self.machine)
        foreign_via_machine = self.make_client_work_order(self.other_machine)
        customer_wins = self.make_work_order(
            customer=self.other_customer, machine=self.machine
        )
        queryset = get_source_adapter('work_order').queryset_for_scope(
            actor=self.actor, scope=self.client_scope
        )
        ids = set(queryset.values_list('pk', flat=True))
        self.assertIn(mine_via_machine.pk, ids)
        self.assertNotIn(explicit_customer.pk, ids)
        self.assertNotIn(foreign_via_machine.pk, ids)
        # An explicit work-order customer owns the order even on my machine.
        self.assertNotIn(customer_wins.pk, ids)

    def test_repair_packet_adapter_membership(self):
        """The explicit work-order customer wins; else the machine's client."""
        from repair.models import RepairPacket

        via_machine = RepairPacket.objects.create(
            fault_summary='a', machine=self.machine
        )
        via_wo = RepairPacket.objects.create(
            fault_summary='b', work_order=self.make_work_order()
        )
        wo_wins = RepairPacket.objects.create(
            fault_summary='w',
            machine=self.other_machine,
            work_order=self.make_work_order(),
        )
        theirs = RepairPacket.objects.create(
            fault_summary='c', machine=self.other_machine
        )
        foreign_wo = RepairPacket.objects.create(
            fault_summary='d',
            machine=self.machine,
            work_order=self.make_work_order(customer=self.other_customer),
        )
        unprovable = RepairPacket.objects.create(fault_summary='e')
        all_pks = {
            via_machine.pk,
            via_wo.pk,
            wo_wins.pk,
            theirs.pk,
            foreign_wo.pk,
            unprovable.pk,
        }
        adapter = get_source_adapter('repair_packet')
        customer_ids = set(
            adapter.queryset_for_scope(actor=self.actor, scope=self.scope).values_list(
                'pk', flat=True
            )
        )
        self.assertEqual(customer_ids & all_pks, {via_wo.pk, wo_wins.pk})
        client_ids = set(
            adapter.queryset_for_scope(
                actor=self.actor, scope=self.client_scope
            ).values_list('pk', flat=True)
        )
        self.assertEqual(client_ids & all_pks, {via_machine.pk})

    def test_approval_adapter_membership(self):
        """Approvals are scoped only through provable links."""
        import uuid as uuid_mod

        from approvals.models import Approval
        from repair.models import RepairPacket, RepairPacketApprovalLink

        def approval():
            return Approval.objects.create(
                action_type='purchase_order',
                summary='spend',
                payload={},
                idempotency_key=uuid_mod.uuid4().hex,
            )

        linked = approval()
        RepairPacketApprovalLink.objects.create(
            packet=RepairPacket.objects.create(fault_summary='p', machine=self.machine),
            approval=linked,
        )
        foreign = approval()
        RepairPacketApprovalLink.objects.create(
            packet=RepairPacket.objects.create(
                fault_summary='q', machine=self.other_machine
            ),
            approval=foreign,
        )
        unlinked = approval()
        adapter = get_source_adapter('approval')
        ids = set(
            adapter.queryset_for_scope(
                actor=self.actor, scope=self.client_scope
            ).values_list('pk', flat=True)
        )
        self.assertIn(linked.pk, ids)
        self.assertNotIn(foreign.pk, ids)
        self.assertNotIn(unlinked.pk, ids)
        # Machine-linked packets belong to clients; customer scopes see none.
        customer_ids = set(
            adapter.queryset_for_scope(actor=self.actor, scope=self.scope).values_list(
                'pk', flat=True
            )
        )
        self.assertEqual(customer_ids & {linked.pk, foreign.pk, unlinked.pk}, set())

    def test_approval_linked_to_two_clients_leaks_nowhere(self):
        """An approval linked to two clients is conflicting: no scope sees it.

        Regression for the ``exclude(Q & ~Q)`` multi-valued join pitfall:
        with one in-scope link and one foreign link on the same relation,
        an un-anchored exclusion never fires and the approval leaks into
        both scopes.
        """
        import uuid as uuid_mod

        from approvals.models import Approval
        from repair.models import RepairPacket, RepairPacketApprovalLink

        conflicted = Approval.objects.create(
            action_type='purchase_order',
            summary='spend',
            payload={},
            idempotency_key=uuid_mod.uuid4().hex,
        )
        RepairPacketApprovalLink.objects.create(
            packet=RepairPacket.objects.create(
                fault_summary='mine', machine=self.machine
            ),
            approval=conflicted,
        )
        RepairPacketApprovalLink.objects.create(
            packet=RepairPacket.objects.create(
                fault_summary='theirs', machine=self.other_machine
            ),
            approval=conflicted,
        )
        adapter = get_source_adapter('approval')
        for scope in (self.client_scope, self.other_client_scope):
            ids = set(
                adapter.queryset_for_scope(actor=self.actor, scope=scope).values_list(
                    'pk', flat=True
                )
            )
            self.assertNotIn(conflicted.pk, ids)

    def test_po_line_adapter_membership(self):
        """PO lines are scoped only via job-kit shortage linkage."""
        from tasks.jobkit_models import JobKit, JobKitLine, JobKitShortage

        from company.models import Company
        from order.models import PurchaseOrder, PurchaseOrderLineItem
        from part.models import Part

        supplier = Company.objects.create(name='Supplier', is_supplier=True)
        order = PurchaseOrder.objects.create(supplier=supplier, reference='PO-9001')
        linked_line = PurchaseOrderLineItem.objects.create(
            order=order, quantity=10, received=0
        )
        unlinked_line = PurchaseOrderLineItem.objects.create(
            order=order, quantity=5, received=0
        )
        part = Part.objects.create(name='Bearing-scope', description='d')
        kit = JobKit.objects.create(
            work_order=self.make_work_order(), created_by=self.actor
        )
        kit_line = JobKitLine.objects.create(
            kit=kit,
            sequence=1,
            kind='part',
            requested_part=part,
            selected_part=part,
            required_quantity=1,
            fulfillment_mode='reserve_consume',
            source='manual',
        )
        JobKitShortage.objects.create(
            line=kit_line, quantity=1, status='ordered', purchase_order_line=linked_line
        )
        queryset = get_source_adapter('purchase_order_line').queryset_for_scope(
            actor=self.actor, scope=self.scope
        )
        ids = set(queryset.values_list('pk', flat=True))
        self.assertIn(linked_line.pk, ids)
        self.assertNotIn(unlinked_line.pk, ids)

    def test_po_line_feeding_two_customers_leaks_nowhere(self):
        """A PO line feeding two customers' kits is excluded from both scopes."""
        from tasks.jobkit_models import JobKit, JobKitLine, JobKitShortage

        from company.models import Company
        from order.models import PurchaseOrder, PurchaseOrderLineItem
        from part.models import Part

        supplier = Company.objects.create(name='Supplier-2', is_supplier=True)
        order = PurchaseOrder.objects.create(supplier=supplier, reference='PO-9002')
        shared_line = PurchaseOrderLineItem.objects.create(
            order=order, quantity=10, received=0
        )
        part = Part.objects.create(name='Shared-scope', description='d')

        def shortage_for(work_order, sequence):
            kit = JobKit.objects.create(work_order=work_order, created_by=self.actor)
            kit_line = JobKitLine.objects.create(
                kit=kit,
                sequence=sequence,
                kind='part',
                requested_part=part,
                selected_part=part,
                required_quantity=1,
                fulfillment_mode='reserve_consume',
                source='manual',
            )
            JobKitShortage.objects.create(
                line=kit_line,
                quantity=1,
                status='ordered',
                purchase_order_line=shared_line,
            )

        shortage_for(self.make_work_order(), 1)
        shortage_for(self.make_work_order(customer=self.other_customer), 1)
        adapter = get_source_adapter('purchase_order_line')
        for scope in (self.scope, self.other_scope):
            ids = set(
                adapter.queryset_for_scope(actor=self.actor, scope=scope).values_list(
                    'pk', flat=True
                )
            )
            self.assertNotIn(shared_line.pk, ids)

    def test_part_adapter_membership(self):
        """Parts are relevant only via the scope client's machines."""
        from assets.models import MachinePart
        from part.models import Part

        installed = Part.objects.create(name='Filter-scope', description='d')
        MachinePart.objects.create(machine=self.machine, part=installed)
        foreign = Part.objects.create(name='Belt-scope', description='d')
        MachinePart.objects.create(machine=self.other_machine, part=foreign)
        loose = Part.objects.create(name='Loose-scope', description='d')
        adapter = get_source_adapter('part_stock')
        ids = set(
            adapter.queryset_for_scope(
                actor=self.actor, scope=self.client_scope
            ).values_list('pk', flat=True)
        )
        self.assertIn(installed.pk, ids)
        self.assertNotIn(foreign.pk, ids)
        self.assertNotIn(loose.pk, ids)

    def test_part_adapter_empty_for_customer_scopes(self):
        """Machines carry no customer identity: customer scopes see no parts."""
        from assets.models import MachinePart
        from part.models import Part

        installed = Part.objects.create(name='Filter-cscope', description='d')
        MachinePart.objects.create(machine=self.machine, part=installed)
        queryset = get_source_adapter('part_stock').queryset_for_scope(
            actor=self.actor, scope=self.scope
        )
        self.assertEqual(queryset.count(), 0)

    def test_machine_adapter_membership(self):
        """Machines are scoped by their client."""
        queryset = get_source_adapter('asset_machine').queryset_for_scope(
            actor=self.actor, scope=self.client_scope
        )
        ids = set(queryset.values_list('pk', flat=True))
        self.assertIn(self.machine.pk, ids)
        self.assertNotIn(self.other_machine.pk, ids)

    def test_machine_adapter_empty_for_customer_scopes(self):
        """A customer scope truthfully owns no machines."""
        queryset = get_source_adapter('asset_machine').queryset_for_scope(
            actor=self.actor, scope=self.scope
        )
        self.assertEqual(queryset.count(), 0)


class ServicePrincipalTest(TestCase):
    """Scans fail closed while the scanner principal is unconfigured."""

    def test_unset_fails_closed(self):
        """Unset AIMMS_RISK_SERVICE_USER_ID raises."""
        with self.assertRaises(RiskScopeError):
            risk_service_user()

    @override_settings(AIMMS_RISK_SERVICE_USER_ID='not-a-number')
    def test_invalid_fails_closed(self):
        """A malformed principal id raises."""
        with self.assertRaises(RiskScopeError):
            risk_service_user()

    def test_resolves_active_user(self):
        """A valid principal id resolves to the user."""
        user = get_user_model().objects.create_user(
            username='svc-x', email='x@example.com', password='pw'
        )
        with override_settings(AIMMS_RISK_SERVICE_USER_ID=str(user.pk)):
            self.assertEqual(risk_service_user().pk, user.pk)

    def test_inactive_user_fails_closed(self):
        """An inactive principal raises."""
        user = get_user_model().objects.create_user(
            username='svc-y', email='y@example.com', password='pw', is_active=False
        )
        with override_settings(AIMMS_RISK_SERVICE_USER_ID=user.pk):
            with self.assertRaises(RiskScopeError):
                risk_service_user()
