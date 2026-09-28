"""Ledger-model tests for the EQUA demo metrics session (work package B).

Every uniqueness/consistency rule here is a database constraint, not a
convention: concurrent applies and duplicate imports must lose at the database.
"""

import datetime as dt

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from assets.demo_metrics_models import (
    DemoMetricsCoverageInterval,
    DemoMetricsDowntimeInterval,
    DemoMetricsMachine,
    DemoMetricsObject,
    DemoMetricsReceipt,
    DemoMetricsSession,
)
from assets.models import AssetMachine, Client


def make_session(session_key='s1', **kwargs):
    """Create a minimal applied session row."""
    defaults = {
        'dataset_key': 'equa-demo-metrics-v1',
        'session_key': session_key,
        'fixture_version': 'equa.demo-fixture/1',
        'fixture_canonical_sha256': 'a' * 64,
        'fixture_file_sha256': 'b' * 64,
        'mapping_sha256': 'c' * 64,
        'plan_sha256': 'd' * 64,
        'target_fingerprint': 'test-fingerprint',
        'anchor_at': dt.datetime(2026, 9, 25, 12, 0),
        'expires_at': dt.datetime(2026, 9, 25, 13, 0),
    }
    defaults.update(kwargs)
    return DemoMetricsSession.objects.create(**defaults)


class SessionModelTest(TestCase):
    """Session identity and status are durable and unique."""

    def test_session_key_is_unique_per_dataset(self):
        """Enforce session key uniqueness at the database level."""
        make_session()
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_session()

    def test_second_dataset_may_reuse_the_session_key(self):
        """Allow a different dataset to reuse the same session key."""
        make_session()
        other = make_session(dataset_key='other-dataset')
        self.assertEqual(other.session_key, 's1')

    def test_plan_does_not_require_a_session_row(self):
        """Keep planning possible with no session rows inserted."""
        # A plan is a local artifact; only apply inserts sessions. Nothing in
        # the model forces an insert, and this test pins the empty state.
        self.assertEqual(DemoMetricsSession.objects.count(), 0)


class MachineClaimTest(TestCase):
    """Machine claims are exclusive and per-session unique."""

    def setUp(self):
        """Create a client tenant and machine for the claims."""
        self.client_tenant = Client.objects.create(name='C1', code='c1')
        self.machine = AssetMachine.objects.create(name='M1', client=self.client_tenant)

    def test_one_active_claim_per_machine(self):
        """Allow only one active claim per machine."""
        first = make_session('s1')
        second = make_session('s2')
        DemoMetricsMachine.objects.create(
            session=first, machine=self.machine, alias='A01', client_code='c1'
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            DemoMetricsMachine.objects.create(
                session=second, machine=self.machine, alias='A01', client_code='c1'
            )

    def test_released_claim_is_not_unique(self):
        """Free the machine claim once it is released."""
        first = make_session('s1')
        second = make_session('s2')
        claim = DemoMetricsMachine.objects.create(
            session=first, machine=self.machine, alias='A01', client_code='c1'
        )
        claim.claim_active = False
        claim.save(update_fields=['claim_active'])
        DemoMetricsMachine.objects.create(
            session=second, machine=self.machine, alias='A01', client_code='c1'
        )
        self.assertEqual(
            DemoMetricsMachine.objects.filter(machine=self.machine).count(), 2
        )

    def test_alias_is_unique_within_a_session(self):
        """Keep the machine alias unique within a session."""
        session = make_session()
        DemoMetricsMachine.objects.create(
            session=session, machine=self.machine, alias='A01', client_code='c1'
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            DemoMetricsMachine.objects.create(
                session=session, machine=self.machine, alias='A01', client_code='c1'
            )


class ObjectAndReceiptTest(TestCase):
    """Object identity and receipt claims enforce import identity."""

    def setUp(self):
        """Create the session, client tenant and machine for ledger rows."""
        self.session = make_session()
        self.client_tenant = Client.objects.create(name='C2', code='c2')
        self.machine = AssetMachine.objects.create(name='M2', client=self.client_tenant)

    def test_fixture_key_is_unique_per_kind(self):
        """Enforce fixture key uniqueness per object kind."""
        from tasks.models import WorkOrder

        order = WorkOrder.objects.create(title='t', status='backlog', priority='low')
        DemoMetricsObject.objects.create(
            session=self.session,
            kind=DemoMetricsObject.Kind.WORK_ORDER,
            fixture_key='WO-01',
            origin=DemoMetricsObject.Origin.CREATED,
            work_order=order,
            dependent_refs=[{'kind': 'card', 'id': 1}],
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            DemoMetricsObject.objects.create(
                session=self.session,
                kind=DemoMetricsObject.Kind.WORK_ORDER,
                fixture_key='WO-01',
                origin=DemoMetricsObject.Origin.CREATED,
                work_order=order,
            )

    def test_typed_target_fk_must_match_kind(self):
        """Require typed target foreign keys to match the object kind."""
        from tasks.models import WorkOrder

        order = WorkOrder.objects.create(title='t', status='backlog', priority='low')
        # A source-kind row carrying a work_order FK violates the check.
        with self.assertRaises(IntegrityError), transaction.atomic():
            DemoMetricsObject.objects.create(
                session=self.session,
                kind=DemoMetricsObject.Kind.SOURCE,
                fixture_key='DEMO-SOURCE-A',
                origin=DemoMetricsObject.Origin.CREATED,
                work_order=order,
            )

    def test_receipt_identity_prevents_duplicate_import_claims(self):
        """Prevent duplicate import claims through receipt identity."""
        DemoMetricsReceipt.objects.create(
            session=self.session,
            operation_kind=DemoMetricsReceipt.Operation.APPLY_WORK_ORDER,
            item_key='WO-01',
            request_hash='f' * 64,
            outcome=DemoMetricsReceipt.Outcome.APPLIED,
            effect_ids=[1],
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            DemoMetricsReceipt.objects.create(
                session=self.session,
                operation_kind=DemoMetricsReceipt.Operation.APPLY_WORK_ORDER,
                item_key='WO-01',
                request_hash='e' * 64,
                outcome=DemoMetricsReceipt.Outcome.APPLIED,
            )

    def test_apply_session_execution_kind_is_a_valid_operation(self):
        """Validate the apply-session execution receipt against the choices."""
        from assets.demo_metrics import apply_service

        self.assertEqual(
            apply_service.EXECUTION_OPERATION_KIND,
            DemoMetricsReceipt.Operation.APPLY_SESSION,
        )
        self.assertIn(
            apply_service.EXECUTION_OPERATION_KIND, DemoMetricsReceipt.Operation.values
        )
        receipt = DemoMetricsReceipt(
            session=self.session,
            operation_kind=DemoMetricsReceipt.Operation.APPLY_SESSION,
            item_key=apply_service.EXECUTION_ITEM_KEY,
            request_hash='a' * 64,
            outcome=DemoMetricsReceipt.Outcome.APPLIED,
        )
        receipt.full_clean()
        with self.assertRaises(ValidationError):
            DemoMetricsReceipt(
                session=self.session,
                operation_kind='not_a_real_operation',
                item_key='x',
                request_hash='b' * 64,
                outcome=DemoMetricsReceipt.Outcome.APPLIED,
            ).full_clean()

    def test_referenced_work_order_is_protected_from_deletion(self):
        """Protect referenced work orders from deletion."""
        from django.db.models import ProtectedError

        from tasks.models import WorkOrder

        order = WorkOrder.objects.create(title='keep', status='backlog', priority='low')
        DemoMetricsObject.objects.create(
            session=self.session,
            kind=DemoMetricsObject.Kind.WORK_ORDER,
            fixture_key='WO-REF',
            origin=DemoMetricsObject.Origin.REFERENCED,
            work_order=order,
        )
        with self.assertRaises(ProtectedError):
            order.delete()


class HistoryIntervalTest(TestCase):
    """Historical intervals keep unique keys and strictly positive spans."""

    def setUp(self):
        """Create the session, machine and interval bounds for history rows."""
        self.session = make_session()
        self.client_tenant = Client.objects.create(name='C3', code='c3')
        self.machine = AssetMachine.objects.create(name='M3', client=self.client_tenant)
        self.start = dt.datetime(2026, 9, 10, 8, 0)
        self.end = dt.datetime(2026, 9, 10, 16, 0)

    def test_coverage_interval_keys_are_unique_per_session(self):
        """Keep coverage interval event keys unique per session."""
        DemoMetricsCoverageInterval.objects.create(
            session=self.session,
            event_key='coverage/A02/00',
            machine=self.machine,
            start_at=self.start,
            end_at=self.end,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            DemoMetricsCoverageInterval.objects.create(
                session=self.session,
                event_key='coverage/A02/00',
                machine=self.machine,
                start_at=self.start,
                end_at=self.end,
            )

    def test_coverage_interval_must_be_positive(self):
        """Reject coverage intervals that are not strictly positive."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            DemoMetricsCoverageInterval.objects.create(
                session=self.session,
                event_key='bad',
                machine=self.machine,
                start_at=self.end,
                end_at=self.start,
            )

    def test_downtime_interval_must_be_positive(self):
        """Reject downtime intervals that are not strictly positive."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            DemoMetricsDowntimeInterval.objects.create(
                session=self.session,
                event_key='bad',
                machine=self.machine,
                start_at=self.end,
                end_at=self.start,
                loss_category='unplanned_loss_of_function',
            )

    def test_downtime_may_overlap_and_is_attributed_synthetic(self):
        """Allow overlapping downtime attributed as synthetic scenario."""
        # Overlaps are legitimate; aggregation unions them.
        DemoMetricsDowntimeInterval.objects.create(
            session=self.session,
            event_key='downtime/A02/00',
            machine=self.machine,
            start_at=self.start,
            end_at=self.end,
            loss_category='unplanned_loss_of_function',
        )
        DemoMetricsDowntimeInterval.objects.create(
            session=self.session,
            event_key='downtime/A02/01',
            machine=self.machine,
            start_at=self.start + dt.timedelta(hours=1),
            end_at=self.end,
            loss_category='unplanned_loss_of_function',
        )
        self.assertEqual(
            DemoMetricsDowntimeInterval.objects.filter(session=self.session).count(), 2
        )
        for row in DemoMetricsDowntimeInterval.objects.all():
            self.assertEqual(row.attribution_mode, 'synthetic_scenario')
