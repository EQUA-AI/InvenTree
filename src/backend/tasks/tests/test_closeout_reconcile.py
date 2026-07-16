"""Tests for parts-usage reconciliation and closeout readings."""

from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings

from stock.models import StockItem
from tasks.closeout_models import (
    CloseoutPartUsage,
    CloseoutPartUsageState,
    CloseoutReadingState,
    PartUsageDisposition,
)
from tasks.models import (
    JobKitAllocation,
    JobKitAllocationStatus,
    WorkOrderDeviation,
    WorkOrderEvent,
)
from tasks.services.closeout_reconcile import (
    NumericAmbiguityBlocking,
    PartVarianceUnresolved,
    ReadingError,
    ReconciliationError,
    add_narrative_candidate,
    add_walkup_usage,
    disposition_reading,
    promote_reading_candidate,
    record_reading,
    refresh_closeout_reconciliation,
    resolve_part_usage,
    unresolved_required_readings,
    unresolved_usage_rows,
)
from tasks.services.job_kit_custody import (
    JobKitCustodyError,
    consume_allocation,
    issue_allocation,
    return_allocation,
)
from tasks.tests.closeout_fixtures import CLOSEOUT_FLAGS, CloseoutEnvMixin


@override_settings(**CLOSEOUT_FLAGS)
class PartUsageReconciliationTest(CloseoutEnvMixin, TestCase):
    """Custody truth seeds the rows; dispositions resolve them."""

    def setUp(self):
        self.build_env(username='recon-user')
        self.line = self.build_kit_line()
        self.reserve_kit()
        self.allocation = JobKitAllocation.objects.get(line=self.line)

    def refresh(self):
        return refresh_closeout_reconciliation(
            work_order_id=self.work_order.pk, actor=self.actor
        )

    def test_refresh_seeds_rows_from_custody_truth(self):
        counts = self.refresh()
        self.assertEqual(counts['created'], 1)
        row = CloseoutPartUsage.objects.get(allocation=self.allocation)
        self.assertEqual(row.planned_quantity, self.line.required_quantity)
        self.assertEqual(row.issued_quantity, self.allocation.quantity)
        self.assertEqual(row.state, CloseoutPartUsageState.PENDING)
        self.assertTrue(
            WorkOrderEvent.objects.filter(
                work_order=self.work_order, event_type='RECONCILIATION_REFRESHED'
            ).exists()
        )

    def test_refresh_is_idempotent(self):
        self.refresh()
        counts = self.refresh()
        self.assertEqual(counts['created'], 0)
        self.assertEqual(CloseoutPartUsage.objects.count(), 1)

    def test_custody_consume_then_refresh_reconciles_with_tracking_id(self):
        issue_allocation(
            work_order_id=self.work_order.pk,
            allocation_id=self.allocation.pk,
            actor=self.actor,
        )
        consume_allocation(
            work_order_id=self.work_order.pk,
            allocation_id=self.allocation.pk,
            actor=self.actor,
        )
        self.refresh()
        row = CloseoutPartUsage.objects.get(allocation=self.allocation)
        self.assertEqual(row.state, CloseoutPartUsageState.RECONCILED)
        self.assertEqual(row.disposition, PartUsageDisposition.CONSUMED)
        self.assertIsNotNone(row.stock_tracking_id)
        self.allocation.refresh_from_db()
        self.assertEqual(row.stock_tracking_id, self.allocation.stock_tracking_id)

    def test_consumed_disposition_requires_custody_consume_first(self):
        self.refresh()
        row = CloseoutPartUsage.objects.get(allocation=self.allocation)
        with self.assertRaises(PartVarianceUnresolved):
            resolve_part_usage(
                work_order_id=self.work_order.pk,
                row_id=row.pk,
                actor=self.actor,
                disposition=PartUsageDisposition.CONSUMED,
            )

    def test_variance_requires_reason(self):
        self.refresh()
        row = CloseoutPartUsage.objects.get(allocation=self.allocation)
        with self.assertRaises(PartVarianceUnresolved):
            resolve_part_usage(
                work_order_id=self.work_order.pk,
                row_id=row.pk,
                actor=self.actor,
                disposition=PartUsageDisposition.SCRAPPED,
                used_quantity=Decimal('1'),
            )
        resolved = resolve_part_usage(
            work_order_id=self.work_order.pk,
            row_id=row.pk,
            actor=self.actor,
            disposition=PartUsageDisposition.SCRAPPED,
            used_quantity=Decimal('1'),
            reason='One unit damaged during install',
        )
        self.assertEqual(resolved.state, CloseoutPartUsageState.RECONCILED)

    def test_returned_tool_reconciles_after_custody_return(self):
        return_allocation(
            work_order_id=self.work_order.pk,
            allocation_id=self.allocation.pk,
            actor=self.actor,
        )
        self.refresh()
        row = CloseoutPartUsage.objects.get(allocation=self.allocation)
        self.assertEqual(row.disposition, PartUsageDisposition.RETURNED)
        self.assertEqual(row.state, CloseoutPartUsageState.RECONCILED)
        self.assertEqual(row.used_quantity, Decimal('0'))

    def test_reconciled_row_reflagged_on_custody_drift(self):
        self.refresh()
        row = CloseoutPartUsage.objects.get(allocation=self.allocation)
        resolve_part_usage(
            work_order_id=self.work_order.pk,
            row_id=row.pk,
            actor=self.actor,
            disposition=PartUsageDisposition.RETURNED,
            used_quantity=Decimal('0'),
            reason='not needed',
        )
        JobKitAllocation.objects.filter(pk=self.allocation.pk).update(
            quantity=Decimal('2')
        )
        counts = self.refresh()
        self.assertEqual(counts['flagged'], 1)
        row.refresh_from_db()
        self.assertEqual(row.state, CloseoutPartUsageState.PENDING)

    def test_stale_row_version_is_rejected(self):
        self.refresh()
        row = CloseoutPartUsage.objects.get(allocation=self.allocation)
        with self.assertRaises(ReconciliationError):
            resolve_part_usage(
                work_order_id=self.work_order.pk,
                row_id=row.pk,
                actor=self.actor,
                disposition=PartUsageDisposition.RETURNED,
                reason='r',
                expected_row_version=99,
            )

    def test_serialized_stock_requires_explicit_manual_disposition(self):
        part = self.line.selected_part
        part.trackable = True
        part.save()
        serial_item = StockItem.objects.create(
            part=part, quantity=Decimal('1'), serial='SN-1'
        )
        JobKitAllocation.objects.filter(pk=self.allocation.pk).update(
            stock_item=serial_item, quantity=Decimal('1')
        )
        self.allocation.refresh_from_db()
        with self.assertRaises(JobKitCustodyError):
            consume_allocation(
                work_order_id=self.work_order.pk,
                allocation_id=self.allocation.pk,
                actor=self.actor,
            )
        self.refresh()
        row = CloseoutPartUsage.objects.get(allocation=self.allocation)
        resolved = resolve_part_usage(
            work_order_id=self.work_order.pk,
            row_id=row.pk,
            actor=self.actor,
            disposition=PartUsageDisposition.SERIALIZED_MANUAL,
            used_quantity=Decimal('1'),
            reason='Serialized unit installed; handled by storeroom manually',
        )
        self.assertEqual(resolved.state, CloseoutPartUsageState.RECONCILED)


@override_settings(**CLOSEOUT_FLAGS)
class WalkupAndCandidateTest(CloseoutEnvMixin, TestCase):
    """Walk-up usage binds to real stock transactions; candidates are explicit."""

    def setUp(self):
        self.build_env(username='walkup-user')
        from part.models import Part

        self.part = Part.objects.create(
            name='Walkup Part', description='w', component=True
        )
        self.stock_item = StockItem.objects.create(
            part=self.part, quantity=Decimal('20')
        )

    def test_walkup_requires_real_tracking_entry(self):
        with self.assertRaises(ReconciliationError):
            add_walkup_usage(
                work_order_id=self.work_order.pk,
                actor=self.actor,
                stock_item_id=self.stock_item.pk,
                used_quantity=Decimal('2'),
                stock_tracking_id=999999,
            )

    def test_walkup_binds_to_existing_stock_transaction(self):
        self.stock_item.take_stock(Decimal('2'), self.actor, notes='walk-up')
        tracking = self.stock_item.tracking_info.order_by('-pk').first()
        row = add_walkup_usage(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            stock_item_id=self.stock_item.pk,
            used_quantity=Decimal('2'),
            stock_tracking_id=tracking.pk,
            reason='grabbed from open stock',
        )
        self.assertEqual(row.state, CloseoutPartUsageState.RECONCILED)
        self.assertEqual(row.source, 'walkup')
        self.assertEqual(row.stock_tracking_id, tracking.pk)

    def test_tracking_entry_must_belong_to_the_stock_item(self):
        other = StockItem.objects.create(part=self.part, quantity=Decimal('5'))
        other.take_stock(Decimal('1'), self.actor, notes='other')
        tracking = other.tracking_info.order_by('-pk').first()
        with self.assertRaises(ReconciliationError):
            add_walkup_usage(
                work_order_id=self.work_order.pk,
                actor=self.actor,
                stock_item_id=self.stock_item.pk,
                used_quantity=Decimal('1'),
                stock_tracking_id=tracking.pk,
            )

    def test_candidate_requires_explicit_dismissal_reason(self):
        row = add_narrative_candidate(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            candidate_text='the 30A contactor',
        )
        self.assertEqual(row.state, CloseoutPartUsageState.BLOCKED)
        variances, candidates = unresolved_usage_rows(self.work_order)
        self.assertEqual(len(candidates), 1)
        with self.assertRaises(ReconciliationError):
            resolve_part_usage(
                work_order_id=self.work_order.pk,
                row_id=row.pk,
                actor=self.actor,
                disposition=PartUsageDisposition.DISMISSED,
            )
        resolved = resolve_part_usage(
            work_order_id=self.work_order.pk,
            row_id=row.pk,
            actor=self.actor,
            disposition=PartUsageDisposition.DISMISSED,
            reason='already covered by kit line 1',
        )
        self.assertEqual(resolved.state, CloseoutPartUsageState.RECONCILED)

    def test_only_candidates_can_be_dismissed(self):
        self.stock_item.take_stock(Decimal('1'), self.actor, notes='walk-up')
        tracking = self.stock_item.tracking_info.order_by('-pk').first()
        row = add_walkup_usage(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            stock_item_id=self.stock_item.pk,
            used_quantity=Decimal('1'),
            stock_tracking_id=tracking.pk,
        )
        with self.assertRaises(ReconciliationError):
            resolve_part_usage(
                work_order_id=self.work_order.pk,
                row_id=row.pk,
                actor=self.actor,
                disposition=PartUsageDisposition.DISMISSED,
                reason='nope',
            )


@override_settings(**CLOSEOUT_FLAGS)
class CloseoutReadingTest(CloseoutEnvMixin, TestCase):
    """Readings: deterministic normalization, ranges, dispositions, evidence."""

    def setUp(self):
        self.build_env(username='reading-user')

    def record(self, raw, **kwargs):
        defaults = {
            'work_order_id': self.work_order.pk,
            'actor': self.actor,
            'label': 'Output pressure',
            'raw_text': raw,
        }
        defaults.update(kwargs)
        return record_reading(**defaults)

    def test_in_range_reading_verifies(self):
        reading = self.record(
            '42 psi', unit='psi', expected_min='40', expected_max='45'
        )
        self.assertEqual(reading.verification_state, CloseoutReadingState.VERIFIED)
        self.assertEqual(reading.value, Decimal('42'))
        self.assertEqual(reading.normalization_rule_version, 'co-norm-1')

    def test_out_of_range_reading_fails(self):
        reading = self.record(
            '55 psi', unit='psi', expected_min='40', expected_max='45'
        )
        self.assertEqual(reading.verification_state, CloseoutReadingState.FAILED)

    def test_ambiguous_reading_stays_pending_with_warning(self):
        reading = self.record('forty–fifty psi', required=True)
        self.assertEqual(reading.verification_state, CloseoutReadingState.PENDING)
        self.assertIsNone(reading.value)
        self.assertIn('numeric_ambiguity', reading.warnings)
        self.assertEqual(unresolved_required_readings(self.work_order), [reading])

    def test_promotion_of_ambiguous_candidate_blocks(self):
        with self.assertRaises(NumericAmbiguityBlocking):
            promote_reading_candidate(raw_text='fifteen–fifty')
        self.assertEqual(promote_reading_candidate(raw_text='23.7'), Decimal('23.7'))

    def test_retest_disposition_creates_replacement_row(self):
        reading = self.record('55', expected_min='40', expected_max='45', required=True)
        resolved, replacement = disposition_reading(
            work_order_id=self.work_order.pk,
            reading_id=reading.pk,
            actor=self.actor,
            disposition='retest',
            reason='Gauge misread; retesting',
        )
        self.assertEqual(
            resolved.verification_state, CloseoutReadingState.DISPOSITIONED
        )
        self.assertIsNotNone(replacement)
        self.assertEqual(
            replacement.verification_state, CloseoutReadingState.PENDING
        )
        self.assertTrue(replacement.required)

    def test_deviation_disposition_links_a_real_deviation(self):
        reading = self.record('55', expected_min='40', expected_max='45')
        disposition_reading(
            work_order_id=self.work_order.pk,
            reading_id=reading.pk,
            actor=self.actor,
            disposition='deviation',
            reason='Running hot pending part delivery',
        )
        self.assertTrue(
            WorkOrderDeviation.objects.filter(
                work_order=self.work_order, category='closeout_reading'
            ).exists()
        )

    def test_supervisor_review_requires_verify_permission(self):
        reading = self.record('55', expected_min='40', expected_max='45')
        technician = self.make_scoped_user(
            'reading-tech', permissions=['capture_closeout']
        )
        with self.assertRaises(PermissionDenied):
            disposition_reading(
                work_order_id=self.work_order.pk,
                reading_id=reading.pk,
                actor=technician,
                disposition='supervisor_review',
                reason='needs supervisor sign-off',
            )

    def test_verified_reading_cannot_be_dispositioned(self):
        reading = self.record('42', expected_min='40', expected_max='45')
        with self.assertRaises(ReadingError):
            disposition_reading(
                work_order_id=self.work_order.pk,
                reading_id=reading.pk,
                actor=self.actor,
                disposition='retest',
                reason='no need',
            )

    def test_evidence_links_require_a_real_attachment(self):
        with self.assertRaises(ReadingError):
            self.record('42', evidence_attachment_ids=[987654])
