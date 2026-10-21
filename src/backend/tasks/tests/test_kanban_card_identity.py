"""Unequal card/work-order identity on the real DB (acceptance verification).

Verification tests for behavior that is already implemented in
``tasks.ai_read`` (``authorized_kanban_card`` / ``kanban_card_row`` /
``kanban_cards_page``) and the card-typed board-task tool pack in
``ai.core.integrations.kanban_tools`` (``list_board_tasks`` /
``get_board_task`` / ``check_board_task_stock``). They pin the acceptance
matrix the pure-contract suite cannot reach, through the real Django ORM and
the real readers — no mocked application modules and no mocked fence
predicates; only the production grant/settings seams
(``AIMMS_MAINTENANCE_SCOPE_RESOLVER``, ``AIMMS_MAINTENANCE_AI_READ_ENABLED``,
``ai.core.auth.principal_context``, ``ai.core.analysis.scope_context``) that
production itself uses.

* a ``KanbanCard`` PK is canonically its own identity — deliberately unequal
  to its parent ``WorkOrder`` PK in this fixture — and every projection names
  ``kanban_card_id`` and ``work_order_id`` separately, never a bare ``id``;
* several cards share one work order and stay independently addressable
  (every work order additionally carries its auto-created ``KIND_WORK_ORDER``
  job card — three cards on the canonical job here);
* the card's board column never overwrites the parent's governed lifecycle;
* title search and page counts are honest (population vs returned, page
  limit 1 reports truncation instead of a false complete/unique claim);
* a revoked grant denies the NEXT real reader call even after a focus
  candidate resolved successfully;
* foreign, missing, deleted and parent-deleted rows deny generically, with
  no fixture names in the denial;
* no principal, a deleted user and the kill switch all fail closed;
* the analysis fence (explicit machine scope) narrows authorized reads and
  never expands authority to machines outside the maintenance grant;
* the legacy WorkOrder-PK tools stay WorkOrder-typed (positive control) and
  reject the other kind's numeric identity.
"""

import uuid
from contextlib import contextmanager

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from asgiref.sync import async_to_sync

from ai.core.analysis.scope import MODE_EXPLICIT, SCHEMA_VERSION, SOURCE_CLASSES
from ai.core.analysis.scope_context import bind_turn_scope, turn_scope_context
from ai.core.auth import AIPrincipal, principal_context
from ai.core.focus.resolution import read_kanban_card
from ai.core.integrations.kanban_tools import (
    check_board_task_stock,
    check_kanban_card_stock,
    get_board_task,
    get_kanban_card,
    list_board_tasks,
)
from assets.models import AssetMachine, Client
from tasks import ai_read
from tasks.models import KanbanCard, WorkOrder, WorkOrderLifecycle
from tasks.scope import MaintenanceScope
from users.models import Owner, RuleSet

READ_FLAGS = {'AIMMS_MAINTENANCE_AI_READ_ENABLED': True}

# The tools reload the acting user from the database by primary key, so an
# in-memory ``maintenance_scopes`` attribute never reaches them. Grants are
# served the way production serves them: through the configured resolver.
_GRANTS: dict[str, set[MaintenanceScope]] = {}


def _grant_resolver(actor):
    """Resolver seam: return whatever the test granted this username."""
    return _GRANTS.get(actor.get_username(), set())


def _principal(user) -> AIPrincipal:
    return AIPrincipal(
        subject=f'user:{user.pk}',
        actor=f'user:{user.pk}',
        user_pk=str(user.pk),
        username=user.get_username(),
        authentication_method='session',
        scope='chat',
        policy_version='test',
        is_staff=bool(user.is_staff),
        is_superuser=bool(user.is_superuser),
    )


@contextmanager
def _acting_as(principal: AIPrincipal | None):
    """Bind (or clear) the boundary principal for the duration of a call."""
    token = principal_context.set(principal)
    try:
        yield
    finally:
        principal_context.reset(token)


@contextmanager
def _turn_scope(*machine_ids):
    """Bind a real explicit analysis scope for the duration of a block.

    Goes through production ``bind_turn_scope`` with the stored snapshot
    shape, so the fence read by the tools is the one production binds.
    """
    snapshot = {
        'scope': {
            'schema_version': SCHEMA_VERSION,
            'mode': MODE_EXPLICIT,
            'machine_ids': list(machine_ids),
            'date_window': {'from': None, 'to': None},
            'source_classes': list(SOURCE_CLASSES),
            'display_label': 'test analysis scope',
        },
        'version': 1,
        'hash': 'b' * 64,
    }
    token = turn_scope_context.set(None)
    try:
        bind_turn_scope(snapshot, thread_pk=None, turn_pk=77)
        yield
    finally:
        turn_scope_context.reset(token)


@override_settings(
    **READ_FLAGS, AIMMS_MAINTENANCE_SCOPE_RESOLVER=f'{__name__}._grant_resolver'
)
class KanbanCardIdentityTestCase(TestCase):
    """Canonical card identity, revocation, coverage truth and fences."""

    @classmethod
    def setUpTestData(cls):
        """Two tenants, three machines, three jobs, unequal-id card sets."""
        suffix = uuid.uuid4().hex[:6]
        cls.suffix = suffix
        cls.client_a = Client.objects.create(
            name=f'CardId Client A {suffix}', code=f'cardid-a-{suffix}'
        )
        cls.client_b = Client.objects.create(
            name=f'CardId Client B {suffix}', code=f'cardid-b-{suffix}'
        )
        cls.machine_a = AssetMachine.objects.create(
            name=f'CardId pump A {suffix}', client=cls.client_a
        )
        cls.machine_a2 = AssetMachine.objects.create(
            name=f'CardId pump A2 {suffix}', client=cls.client_a
        )
        cls.machine_b = AssetMachine.objects.create(
            name=f'CardId press B {suffix}', client=cls.client_b
        )
        # Deliberate disjoint PK ranges: every canonical card PK here differs
        # from every work-order PK (especially its own parent's), so no test
        # can accidentally pass on equal-numbered identities. Job titles avoid
        # the search tokens used below so only CARD titles match them.
        cls.base = base = (
            max(
                WorkOrder.objects.order_by('-pk').values_list('pk', flat=True).first()
                or 0,
                KanbanCard.objects.order_by('-pk').values_list('pk', flat=True).first()
                or 0,
            )
            + 1000
        )
        cls.wo = WorkOrder.objects.create(
            pk=base + 1,
            title='Rebuild the bearing assembly',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=cls.machine_a,
            lifecycle_status=WorkOrderLifecycle.VERIFYING,
        )
        cls.wo2 = WorkOrder.objects.create(
            pk=base + 2,
            title='Second maintenance job',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=cls.machine_a2,
        )
        cls.foreign_wo = WorkOrder.objects.create(
            pk=base + 3,
            title='Foreign press overhaul',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=cls.machine_b,
        )
        # ``WorkOrder.save`` auto-creates the KIND_WORK_ORDER job card for
        # every job (the card that tracks the job itself). Those are real
        # rows and ride every count alongside the explicitly created cards.
        cls.job_card_1 = cls.wo.cards.get(card_kind=KanbanCard.KIND_WORK_ORDER)
        cls.job_card_2 = cls.wo2.cards.get(card_kind=KanbanCard.KIND_WORK_ORDER)
        cls.job_card_foreign = cls.foreign_wo.cards.get(
            card_kind=KanbanCard.KIND_WORK_ORDER
        )
        cls.card_1 = KanbanCard.objects.create(
            pk=base + 20,
            work_order=cls.wo,
            title='Diagnose pump fault',
            status='backlog',
        )
        cls.card_2 = KanbanCard.objects.create(
            pk=base + 21,
            work_order=cls.wo,
            title='Source pump seal kit',
            status='review',
        )
        cls.card_3 = KanbanCard.objects.create(
            pk=base + 22, work_order=cls.wo2, title='Book the crane', status='backlog'
        )
        cls.foreign_card = KanbanCard.objects.create(
            pk=base + 23,
            work_order=cls.foreign_wo,
            title='Foreign pump diagnosis',
            status='backlog',
        )
        cls.actor = get_user_model().objects.create_user(
            username=f'cardid-actor-{suffix}',
            email=f'cardid-{suffix}@example.com',
            password='pw',
        )
        # O24 requires native Work view before applying client grants. Keep
        # this positive-control actor an ordinary reader; the exact client
        # resolver below still governs every machine-only Work/task read.
        group = Group.objects.create(name=f'cardid-viewers-{suffix}')
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        cls.actor.groups.add(group)

    def setUp(self):
        """One grant (client A) through the production resolver seam."""
        _GRANTS.clear()
        self.addCleanup(_GRANTS.clear)
        _GRANTS[self.actor.get_username()] = {
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.client_a.pk
            )
        }
        token = turn_scope_context.set(None)
        self.addCleanup(turn_scope_context.reset, token)

    def _principal(self, user=None) -> AIPrincipal:
        return _principal(user or self.actor)

    def _call(self, tool, principal: AIPrincipal | str | None = 'actor', /, **kwargs):
        bound = self._principal() if principal == 'actor' else principal
        with _acting_as(bound):
            return async_to_sync(tool)(**kwargs)

    def _missing_id(self) -> int:
        return self.base + 999

    # -- canonical targeting: card PK is never the work-order PK -------------

    def test_card_pk_is_canonical_and_never_the_parent_work_order_pk(self):
        """The two identities are distinct everywhere they are named."""
        for card in (
            self.job_card_1,
            self.card_1,
            self.card_2,
            self.card_3,
            self.foreign_card,
        ):
            with self.subTest(card=card.pk):
                self.assertNotEqual(card.pk, card.work_order_id)
        pair = ai_read.authorized_kanban_card(self.actor, self.card_1.pk)
        self.assertIsNotNone(pair)
        card, work_order = pair
        row = ai_read.kanban_card_row(card, work_order=work_order)
        self.assertEqual(row['kanban_card_id'], self.card_1.pk)
        self.assertEqual(row['work_order_id'], self.wo.pk)
        self.assertNotEqual(row['kanban_card_id'], row['work_order_id'])
        # No ambiguous bare identity, and the machine is the PARENT's.
        self.assertNotIn('id', row)
        self.assertEqual(row['machine_id'], self.machine_a.pk)
        self.assertNotEqual(row['machine_id'], self.machine_b.pk)

    def test_two_cards_on_one_work_order_resolve_independently(self):
        """One job, several cards: each stays its own addressable identity."""
        first = ai_read.authorized_kanban_card(self.actor, self.card_1.pk)
        second = ai_read.authorized_kanban_card(self.actor, self.card_2.pk)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        card_a, wo_a = first
        card_b, wo_b = second
        self.assertEqual(card_a.pk, self.card_1.pk)
        self.assertEqual(card_b.pk, self.card_2.pk)
        self.assertNotEqual(card_a.pk, card_b.pk)
        self.assertEqual(wo_a.pk, wo_b.pk)
        self.assertEqual(wo_a.pk, self.wo.pk)
        page = ai_read.kanban_cards_page(self.actor, work_order_id=self.wo.pk)
        self.assertEqual(
            sorted(r['kanban_card_id'] for r in page['rows']),
            sorted([self.job_card_1.pk, self.card_1.pk, self.card_2.pk]),
        )
        self.assertEqual({r['work_order_id'] for r in page['rows']}, {self.wo.pk})

    def test_board_column_status_never_overwrites_parent_lifecycle(self):
        """A column move is execution tracking, not a lifecycle transition."""
        card, work_order = ai_read.authorized_kanban_card(self.actor, self.card_1.pk)
        row = ai_read.kanban_card_row(card, work_order=work_order)
        self.assertEqual(row['board_column_status'], 'backlog')
        self.assertEqual(
            row['work_order_lifecycle_status'], WorkOrderLifecycle.VERIFYING
        )
        # Move the card to another board column through the ORM.
        KanbanCard.objects.filter(pk=self.card_1.pk).update(status='review')
        card, work_order = ai_read.authorized_kanban_card(self.actor, self.card_1.pk)
        row = ai_read.kanban_card_row(card, work_order=work_order)
        self.assertEqual(row['board_column_status'], 'review')
        self.assertEqual(
            row['work_order_lifecycle_status'], WorkOrderLifecycle.VERIFYING
        )
        self.wo.refresh_from_db()
        self.assertEqual(self.wo.lifecycle_status, WorkOrderLifecycle.VERIFYING)
        self.assertEqual(self.wo.status, WorkOrder.STATUS_BACKLOG)

    # -- truthful population coverage ----------------------------------------

    def test_title_search_counts_are_truthful_across_tenants(self):
        """Search counts name the real population; foreign rows never ride."""
        page = ai_read.kanban_cards_page(self.actor, query='pump')
        self.assertEqual(page['population_count'], 2)
        self.assertEqual(page['returned_count'], 2)
        self.assertFalse(page['display_truncated'])
        self.assertEqual(
            [r['kanban_card_id'] for r in page['rows']],
            [self.card_1.pk, self.card_2.pk],
        )
        self.assertNotIn('Foreign', str(page['rows']))
        single = ai_read.kanban_cards_page(self.actor, query='seal')
        self.assertEqual(single['population_count'], 1)
        self.assertEqual(
            [r['kanban_card_id'] for r in single['rows']], [self.card_2.pk]
        )
        self.assertFalse(single['display_truncated'])
        listed = self._call(list_board_tasks, search='pump')
        self.assertEqual(listed['population_count'], 2)
        self.assertEqual(
            [r['kanban_card_id'] for r in listed['tasks']],
            [self.card_1.pk, self.card_2.pk],
        )
        self.assertNotIn('Foreign', str(listed))

    def test_page_limit_one_reports_truncation_not_false_completeness(self):
        """limit=1 over a 2-card match must claim truncation, not uniqueness.

        This surface's coverage vocabulary is ``population_count`` /
        ``returned_count`` / ``display_truncated`` (+ the retrieval
        envelope's ``complete_population``); there is no bare ``has_more``
        key to assert.
        """
        page = ai_read.kanban_cards_page(self.actor, query='pump', limit=1)
        self.assertEqual(page['population_count'], 2)
        self.assertEqual(page['returned_count'], 1)
        self.assertTrue(page['display_truncated'])
        self.assertEqual([r['kanban_card_id'] for r in page['rows']], [self.card_1.pk])
        listed = self._call(list_board_tasks, search='pump', limit=1)
        self.assertEqual(listed['population_count'], 2)
        self.assertEqual(listed['returned_count'], 1)
        self.assertTrue(listed['display_truncated'])
        coverage = listed['retrieval']['coverage']
        self.assertEqual(coverage['population_count'], 2)
        self.assertEqual(coverage['returned_count'], 1)
        self.assertFalse(coverage['complete_population'])
        self.assertTrue(coverage['display_truncated'])

    # -- authorization revocation --------------------------------------------

    def test_revoked_grant_rejects_the_next_real_reader_call(self):
        """Revocation bites at the next call, after a successful resolution."""
        resolved = read_kanban_card(self.actor, self.card_1.pk)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved[0].pk, self.card_1.pk)
        self.assertEqual(resolved[1].pk, self.wo.pk)
        # The grant is revoked only now, after the candidate resolved.
        _GRANTS[self.actor.get_username()] = set()
        self.assertIsNone(read_kanban_card(self.actor, self.card_1.pk))
        self.assertIsNone(ai_read.authorized_kanban_card(self.actor, self.card_1.pk))
        self.assertEqual(
            self._call(get_board_task, kanban_card_id=self.card_1.pk),
            {'error': f'Board task {self.card_1.pk} not found.'},
        )
        page = ai_read.kanban_cards_page(self.actor)
        self.assertEqual(page['population_count'], 0)
        self.assertEqual(page['returned_count'], 0)
        self.assertEqual(page['rows'], [])
        listed = self._call(list_board_tasks)
        self.assertEqual(listed['population_count'], 0)
        self.assertEqual(listed['tasks'], [])

    # -- denials stay generic, without fixture names --------------------------

    def test_foreign_missing_deleted_and_broken_parent_deny_without_names(self):
        """Denied cards never reveal names or their parent identities."""
        self.assertIsNone(
            ai_read.authorized_kanban_card(self.actor, self.foreign_card.pk)
        )
        foreign = self._call(get_board_task, kanban_card_id=self.foreign_card.pk)
        missing = self._call(get_board_task, kanban_card_id=self._missing_id())
        self.assertIn('error', foreign)
        self.assertIn('error', missing)
        self.assertEqual(set(foreign), set(missing))
        self.assertEqual(
            foreign['error'].replace(str(self.foreign_card.pk), '<id>'),
            missing['error'].replace(str(self._missing_id()), '<id>'),
        )
        for blob in (foreign, missing):
            self.assertNotIn('Foreign', str(blob))
            self.assertNotIn(self.client_b.name, str(blob))
            self.assertNotIn('diagnosis', str(blob))
        stock_foreign = self._call(
            check_board_task_stock, kanban_card_id=self.foreign_card.pk
        )
        self.assertIn('error', stock_foreign)
        self.assertNotIn('Foreign', str(stock_foreign))

        # A deleted card denies like a missing one, with no title residue.
        doomed = KanbanCard.objects.create(
            work_order=self.wo, title='Doomed diagnosis', status='backlog'
        )
        self.assertNotEqual(doomed.pk, self.wo.pk)
        self.assertIsNotNone(read_kanban_card(self.actor, doomed.pk))
        doomed.delete()
        self.assertIsNone(ai_read.authorized_kanban_card(self.actor, doomed.pk))
        gone = self._call(get_board_task, kanban_card_id=doomed.pk)
        self.assertEqual(gone, {'error': f'Board task {doomed.pk} not found.'})
        self.assertNotIn('Doomed', str(gone))

        # Broken parent: the production FK cascade consumes the card when
        # its parent is ACTUALLY deleted. Production constraints are never
        # disabled to fabricate a corrupt relational graph.
        doomed_wo = WorkOrder.objects.create(
            title='Doomed parent',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=self.machine_a2,
        )
        child = KanbanCard.objects.create(
            work_order=doomed_wo,
            card_kind=KanbanCard.KIND_SUBTASK,
            title='Child of doomed',
            status='backlog',
        )
        job_card = doomed_wo.cards.get(card_kind=KanbanCard.KIND_WORK_ORDER)
        self.assertNotEqual(child.pk, doomed_wo.pk)
        doomed_wo.delete()
        self.assertFalse(
            KanbanCard.objects.filter(pk__in=[child.pk, job_card.pk]).exists(),
            'CASCADE deletion must leave no orphan card row',
        )
        self.assertIsNone(ai_read.authorized_kanban_card(self.actor, child.pk))
        broken = self._call(get_board_task, kanban_card_id=child.pk)
        self.assertEqual(broken, {'error': f'Board task {child.pk} not found.'})
        self.assertNotIn('Doomed', str(broken))
        self.assertNotIn('Child of doomed', str(broken))

    # -- fail closed ----------------------------------------------------------

    def test_no_principal_deleted_user_and_flag_off_fail_closed(self):
        """Absent authority or a disabled read feature rejects card access."""
        # No boundary identity at all.
        self.assertIsNone(ai_read.authorized_kanban_card(None, self.card_1.pk))
        self.assertEqual(ai_read.kanban_cards_page(None)['rows'], [])
        self.assertEqual(
            self._call(get_board_task, None, kanban_card_id=self.card_1.pk),
            {'error': f'Board task {self.card_1.pk} not found.'},
        )
        none_list = self._call(list_board_tasks, None)
        self.assertEqual(none_list['population_count'], 0)
        self.assertEqual(none_list['tasks'], [])

        # A principal whose user row is gone resolves to nothing. The fixture
        # provisions the Owner row production's ``create_owner`` signal would
        # create outside read-only management commands (this runner's argv
        # suppresses that signal), so the REAL ``post_delete`` user path runs.
        ghost = get_user_model().objects.create_user(
            username=f'cardid-ghost-{self.suffix}',
            email=f'cardid-ghost-{self.suffix}@example.com',
            password='pw',
        )
        ghost_principal = self._principal(ghost)
        ghost_pk = ghost.pk
        Owner.create(obj=ghost)
        ghost.delete()
        self.assertFalse(
            get_user_model().objects.filter(pk=ghost_pk).exists(),
            'the production user-delete path must actually delete the row',
        )
        self.assertEqual(
            self._call(get_board_task, ghost_principal, kanban_card_id=self.card_1.pk),
            {'error': f'Board task {self.card_1.pk} not found.'},
        )
        ghost_list = self._call(list_board_tasks, ghost_principal)
        self.assertEqual(ghost_list['population_count'], 0)
        self.assertEqual(ghost_list['tasks'], [])

        # The kill switch closes every rail.
        with self.settings(AIMMS_MAINTENANCE_AI_READ_ENABLED=False):
            self.assertIsNone(
                ai_read.authorized_kanban_card(self.actor, self.card_1.pk)
            )
            self.assertEqual(ai_read.kanban_cards_page(self.actor)['rows'], [])
            self.assertEqual(
                self._call(get_board_task, kanban_card_id=self.card_1.pk),
                {'error': f'Board task {self.card_1.pk} not found.'},
            )
            off_list = self._call(list_board_tasks)
            self.assertEqual(off_list['population_count'], 0)
            self.assertEqual(off_list['tasks'], [])
            self.assertEqual(
                self._call(check_board_task_stock, kanban_card_id=self.card_1.pk),
                {'error': f'Board task {self.card_1.pk} not found.'},
            )

    # -- analysis fence: narrows only, never expands authority ----------------

    def test_analysis_fence_narrows_and_never_expands_authority(self):
        """Explicit machine scope is a narrowing AFTER authorization."""
        # A turn scope naming the OTHER tenant's machine must not widen the
        # maintenance grant: nothing becomes reachable.
        with _turn_scope(self.machine_b.pk):
            listed = self._call(list_board_tasks)
            self.assertEqual(listed['population_count'], 0)
            self.assertEqual(listed['returned_count'], 0)
            self.assertEqual(listed['tasks'], [])
            self.assertEqual(
                self._call(get_board_task, kanban_card_id=self.card_1.pk),
                {'error': f'Board task {self.card_1.pk} not found.'},
            )
            self.assertEqual(
                self._call(check_board_task_stock, kanban_card_id=self.card_1.pk),
                {'error': f'Board task {self.card_1.pk} not found.'},
            )
            self.assertEqual(
                self._call(get_board_task, kanban_card_id=self.foreign_card.pk),
                {'error': f'Board task {self.foreign_card.pk} not found.'},
            )

        # A turn scope naming the granted machine narrows the grant: only
        # that machine's cards ride, and the fence closes for authorized
        # cards of other machines.
        with _turn_scope(self.machine_a.pk):
            listed = self._call(list_board_tasks)
            self.assertEqual(listed['population_count'], 3)
            self.assertEqual(
                sorted(r['kanban_card_id'] for r in listed['tasks']),
                sorted([self.job_card_1.pk, self.card_1.pk, self.card_2.pk]),
            )
            page = ai_read.kanban_cards_page(
                self.actor, machine_ids=[self.machine_a.pk]
            )
            self.assertEqual(page['population_count'], 3)
            got = self._call(get_board_task, kanban_card_id=self.card_1.pk)
            self.assertEqual(got['kanban_card_id'], self.card_1.pk)
            self.assertEqual(got['work_order_id'], self.wo.pk)
            self.assertEqual(
                self._call(get_board_task, kanban_card_id=self.card_3.pk),
                {'error': f'Board task {self.card_3.pk} not found.'},
            )
            stock = self._call(check_board_task_stock, kanban_card_id=self.card_1.pk)
            self.assertNotIn('error', stock)
            self.assertEqual(stock['kanban_card_id'], self.card_1.pk)
            self.assertEqual(stock['work_order_id'], self.wo.pk)
            self.assertNotEqual(stock['kanban_card_id'], stock['work_order_id'])
            self.assertEqual(stock['message'], 'No parts attached to this work order.')

        # Control: without a fence the authorized card of machine A2 resolves
        # — the denials above are the fence's, not a broken fixture.
        outside = self._call(get_board_task, kanban_card_id=self.card_3.pk)
        self.assertEqual(outside['kanban_card_id'], self.card_3.pk)
        self.assertEqual(outside['work_order_id'], self.wo2.pk)

    # -- legacy WorkOrder-PK tools stay WorkOrder-typed ------------------------

    def test_legacy_tools_stay_work_order_typed_and_reject_the_other_kind(self):
        """Positive control + wrong-kind numeric identity negatives."""
        legacy = self._call(get_kanban_card, work_order_id=self.wo.pk)
        self.assertEqual(legacy.get('id'), self.wo.pk)
        legacy_stock = self._call(check_kanban_card_stock, work_order_id=self.wo.pk)
        self.assertNotIn('error', legacy_stock)
        self.assertEqual(legacy_stock['card_id'], self.wo.pk)

        wrong_get = self._call(get_kanban_card, work_order_id=self.card_1.pk)
        wrong_stock = self._call(check_kanban_card_stock, work_order_id=self.card_1.pk)
        card_side_get = self._call(get_board_task, kanban_card_id=self.wo.pk)
        card_side_stock = self._call(check_board_task_stock, kanban_card_id=self.wo.pk)
        for blob in (wrong_get, wrong_stock, card_side_get, card_side_stock):
            self.assertIn('error', blob)
            self.assertNotIn('Bearing', str(blob))
            self.assertNotIn('Diagnose', str(blob))

        typed = self._call(get_board_task, kanban_card_id=self.card_1.pk)
        self.assertEqual(typed['kanban_card_id'], self.card_1.pk)
        self.assertEqual(typed['work_order_id'], self.wo.pk)
        self.assertNotIn('id', typed)
