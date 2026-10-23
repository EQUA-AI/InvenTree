"""Installed identity is an explicit decision, separate from consumption."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from part.models import Part
from tasks.closeout_models import (
    CloseoutPartUsage,
    CloseoutPartUsageState,
    PartUsageDisposition,
)
from tasks.models import JobKitAllocation
from tasks.services.closeout_reconcile import (
    add_walkup_usage,
    refresh_closeout_reconciliation,
    resolve_part_usage,
)
from tasks.services.job_kit_custody import consume_allocation, issue_allocation
from tasks.tests.closeout_fixtures import CLOSEOUT_FLAGS, CloseoutEnvMixin


@override_settings(**CLOSEOUT_FLAGS)
class RecordedFitReconciliationTests(CloseoutEnvMixin, TestCase):
    """Run real custody/substitution commands without external stock or DBs."""

    def setUp(self):
        """Reserve one scoped kit line and its actual stock."""
        self.build_env(username='recorded-fit-recon')
        self.line = self.build_kit_line()
        self.reserve_kit()
        self.allocation = JobKitAllocation.objects.get(line=self.line)

    def refresh(self):
        """Refresh only this synthetic Work's native reconciliation."""
        return refresh_closeout_reconciliation(
            work_order_id=self.work_order.pk, actor=self.actor
        )

    def installation_slot(self):
        """Give the consumed lot an exact registry slot to expose false promotion."""
        from assets.registry_models import AssetComponent

        item = self.allocation.stock_item
        item.batch = 'CONSUMED-LOT'
        item.save(update_fields=['batch'])
        slot = AssetComponent.objects.create(
            machine=self.machine, code='FIT:1', name='Fit', part=item.part
        )
        self.work_order.affected_component_ref = slot.code
        self.work_order.save(update_fields=['affected_component_ref'])
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
        self.allocation.refresh_from_db()
        return slot

    def test_refresh_and_walkup_consumption_never_verify_an_installation(self):
        """A refresh actor or a usage recorder did not choose installed."""
        from assets.record_models import ComponentFit

        self.installation_slot()
        self.refresh()
        from stock.models import StockItemTracking

        row = CloseoutPartUsage.objects.get(allocation=self.allocation)
        self.assertEqual(row.disposition, PartUsageDisposition.CONSUMED)
        # Consumption is attributed to whoever issued the stock, not chosen by
        # the refresh; neither makes the line a verified installation.
        issue = StockItemTracking.objects.get(pk=row.stock_tracking_id)
        self.assertEqual(row.resolved_by_id, issue.user_id)
        self.assertFalse(ComponentFit.objects.exists())
        add_walkup_usage(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            stock_item_id=self.allocation.stock_item_id,
            used_quantity=1,
            stock_tracking_id=self.allocation.stock_tracking_id,
        )
        self.assertFalse(ComponentFit.objects.exists())

    def test_consumed_old_stock_after_approved_substitution_keeps_reconciliation_available(
        self,
    ):
        """A selected-part change cannot turn old custody stock into the new fit."""
        from assets.record_models import ComponentFit
        from tasks.services.job_kits import decide_substitution, propose_substitution

        slot = self.installation_slot()
        alternate = Part.objects.create(name='Replacement catalogue', component=True)
        self.line.substitution_policy = 'supervisor'
        self.line.save(update_fields=['substitution_policy'])
        proposal = propose_substitution(
            work_order_id=self.work_order.pk,
            line_id=self.line.pk,
            proposed_part_id=alternate.pk,
            actor=self.actor,
            basis={'reason': 'Reviewed alternate'},
        )
        reviewer = get_user_model().objects.create_superuser(
            'sub-reviewer', 'sub@example.org', 'pw'
        )
        reviewer.maintenance_scopes = self.actor.maintenance_scopes
        decide_substitution(
            work_order_id=self.work_order.pk,
            substitution_id=proposal.pk,
            actor=reviewer,
            approve=True,
        )
        slot.part = alternate
        slot.save(update_fields=['part'])
        self.refresh()
        row = CloseoutPartUsage.objects.get(allocation=self.allocation)
        self.assertNotEqual(row.part_id, row.stock_item.part_id)
        self.assertEqual(row.state, CloseoutPartUsageState.RECONCILED)
        resolve_part_usage(
            work_order_id=self.work_order.pk,
            row_id=row.pk,
            actor=self.actor,
            disposition=PartUsageDisposition.SPARE_INSTALLED,
            reason='Review mismatched custody',
        )
        self.refresh()
        self.assertFalse(ComponentFit.objects.exists())
