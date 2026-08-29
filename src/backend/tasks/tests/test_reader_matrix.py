"""S5 (WP-A3): the eight-fixture reader matrix (§13.1).

Every source-reader behavior the analysis rail depends on, pinned against
one world containing all eight §13.1 fixtures:

1. an authorized in-scope active ("solar-like") machine;
2. an authorized but INACTIVE ("water-like") machine — still historically
   readable, status displayed;
3. an evaluation-fixture machine under the ``eval-fixtures`` client;
4. an unauthorized other-client machine;
5. empty in-scope results (a query matching nothing);
6. more than 25 records (the page bound);
7. records with missing dates;
8. duplicate/ambiguous machine names.

The analysis-scope kwargs are exercised in both shadow (counted) and
enforce (filtered) forms, with the zero-result-no-broadening invariant
pinned: an enforced scope's misses stay misses.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from assets import ai_read as assets_ai_read
from assets.models import AssetMachine, Client
from tasks import ai_read
from tasks.models import WorkOrder
from tasks.scope import MaintenanceScope

FLAGS = {
    'AIMMS_MAINTENANCE_AI_READ_ENABLED': True,
    'AIMMS_MACHINE_AI_READ_ENABLED': True,
}


@override_settings(**FLAGS)
class ReaderMatrixTests(TestCase):
    """work_orders_page / machines_page against the §13.1 world."""

    @classmethod
    def setUpTestData(cls):
        """One tenant, the eight fixtures, thirty work orders."""
        user_model = get_user_model()
        cls.actor = user_model.objects.create_user(username='matrix-actor')
        cls.tenant = Client.objects.create(name='Solar Plant', code='solar-plant')
        cls.other_client = Client.objects.create(name='Other Plant', code='other-plant')
        cls.eval_client = Client.objects.create(
            name='Evaluation Fixtures', code='eval-fixtures'
        )

        cls.solar = AssetMachine.objects.create(
            name='Inverter Alpha', client=cls.tenant, serial='SOL-001', active=True
        )
        cls.water = AssetMachine.objects.create(
            name='Old Water Pump', client=cls.tenant, serial='WAT-001', active=False
        )
        # Near-duplicate names, both in scope (the ambiguity fixture —
        # AssetMachine.name is unique, so real-world ambiguity is two names
        # one spoken query matches).
        cls.twin_a = AssetMachine.objects.create(
            name='Feed Pump East', client=cls.tenant, serial='FP-A', active=True
        )
        cls.twin_b = AssetMachine.objects.create(
            name='Feed Pump West', client=cls.tenant, serial='FP-B', active=True
        )
        cls.eval_machine = AssetMachine.objects.create(
            name='HX-200 Eval', client=cls.eval_client, serial='EVAL-HX200-M', active=True
        )
        cls.foreign = AssetMachine.objects.create(
            name='Foreign Press', client=cls.other_client, serial='FRN-001', active=True
        )

        def wo(title, machine, **overrides):
            values = {
                'title': title,
                'machine': machine,
                'status': WorkOrder.STATUS_BACKLOG,
                'priority': WorkOrder.PRIORITY_MEDIUM,
            }
            values.update(overrides)
            return WorkOrder.objects.create(**values)

        # >25 in-scope records on the solar machine (the page-bound fixture).
        for index in range(28):
            wo(f'Solar job {index:02d}', cls.solar)
        cls.water_wo = wo('Water pump overhaul', cls.water)
        # Missing dates: no due date, no schedule (the default shape).
        cls.dateless_wo = wo('Dateless job', cls.twin_a)
        cls.eval_wo = wo('Eval-only job', cls.eval_machine)
        cls.foreign_wo = wo('Foreign job', cls.foreign)

    def setUp(self):
        """Grant the actor its own tenant only, via the attribute seam."""
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk)
        }

    # ---- honest coverage --------------------------------------------------

    def test_population_vs_returned_on_the_page_bound(self):
        """Fixture 6: a 25-row page from 30 reports 30/25, never "25"."""
        page = ai_read.work_orders_page(self.actor, limit=25)
        self.assertEqual(page['population_count'], 30)  # 28 solar + water + dateless
        self.assertEqual(page['returned_count'], 25)
        self.assertFalse(page['complete_population'])
        self.assertTrue(page['display_truncated'])
        self.assertIsNotNone(page['high_watermark'])

    def test_empty_in_scope_result_is_honest_and_complete(self):
        """Fixture 5: zero results are complete, not truncated."""
        page = ai_read.work_orders_page(self.actor, query='no such job anywhere')
        self.assertEqual(page['population_count'], 0)
        self.assertEqual(page['returned_count'], 0)
        self.assertTrue(page['complete_population'])
        self.assertFalse(page['display_truncated'])

    def test_other_client_and_eval_fixtures_never_appear(self):
        """Fixtures 3+4: authorization excludes them before any scope logic."""
        page = ai_read.work_orders_page(self.actor, limit=25)
        titles = {wo.title for wo in page['rows']}
        self.assertNotIn('Foreign job', titles)
        self.assertNotIn('Eval-only job', titles)
        self.assertEqual(
            ai_read.work_orders_page(self.actor, query='Eval-only')['population_count'], 0
        )

    def test_inactive_machine_history_stays_readable_with_status(self):
        """Fixture 2: decommissioned assets remain analyzable; status shows."""
        page = ai_read.work_orders_page(self.actor, query='Water pump')
        self.assertEqual(page['population_count'], 1)
        machines = assets_ai_read.machines_page(self.actor, query='Old Water Pump')
        self.assertEqual(machines['population_count'], 1)
        row = assets_ai_read.machine_search_row(machines['rows'][0])
        self.assertFalse(row['active'])

    def test_duplicate_names_both_stay_visible(self):
        """Fixture 8: ambiguity is surfaced, never silently resolved."""
        machines = assets_ai_read.machines_page(self.actor, query='Feed Pump')
        self.assertEqual(machines['population_count'], 2)

    # ---- analysis-scope narrowing ----------------------------------------

    def test_enforced_scope_filters_after_authorization(self):
        """Enforce narrows to the scoped machines, on top of authorization."""
        page = ai_read.work_orders_page(
            self.actor,
            limit=25,
            scope_machine_ids={self.water.pk, self.twin_a.pk},
            enforce=True,
        )
        self.assertEqual(page['population_count'], 2)
        self.assertEqual(
            {wo.title for wo in page['rows']}, {'Water pump overhaul', 'Dateless job'}
        )
        self.assertEqual(page['applied_filters']['machine_ids'],
                         sorted([self.water.pk, self.twin_a.pk]))

    def test_enforced_scope_zero_result_never_broadens(self):
        """The no-broadening pin: a scope with no matching rows stays empty."""
        page = ai_read.work_orders_page(
            self.actor,
            query='Solar job',
            scope_machine_ids={self.water.pk},
            enforce=True,
        )
        self.assertEqual(page['population_count'], 0)
        self.assertEqual(page['rows'], [])

    def test_enforced_scope_cannot_widen_authorization(self):
        """Scope ids outside the actor's tenant grant nothing (narrowing only)."""
        page = ai_read.work_orders_page(
            self.actor,
            scope_machine_ids={self.foreign.pk, self.eval_machine.pk},
            enforce=True,
        )
        self.assertEqual(page['population_count'], 0)

    def test_shadow_scope_counts_without_filtering(self):
        """Shadow leaves results intact and counts the would-be exclusions."""
        page = ai_read.work_orders_page(
            self.actor,
            limit=25,
            scope_machine_ids={self.water.pk},
            enforce=False,
        )
        self.assertEqual(page['population_count'], 30)
        self.assertGreater(page['out_of_scope_count'], 0)

    def test_enforced_date_window_uses_the_declared_field(self):
        """The date window filters on the declared created_at field."""
        page = ai_read.work_orders_page(
            self.actor,
            scope_machine_ids={self.solar.pk},
            scope_date_from='2000-01-01',
            scope_date_to='2000-01-02',
            enforce=True,
        )
        self.assertEqual(page['population_count'], 0)
        self.assertEqual(page['applied_filters']['date_field'], 'created_at')
        self.assertEqual(page['applied_filters']['from'], '2000-01-01')

    def test_machines_page_enforced_scope(self):
        """machines_page honors the same enforce contract."""
        machines = assets_ai_read.machines_page(
            self.actor, scope_machine_ids={self.solar.pk}, enforce=True
        )
        self.assertEqual(machines['population_count'], 1)
        self.assertEqual(machines['rows'][0].pk, self.solar.pk)
