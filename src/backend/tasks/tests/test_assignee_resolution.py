"""Tests for the assignee -> assigned_to back-fill (S3b).

Two layers: the pure resolution function (username/full-name matching, conservative
on ambiguity) and the migration's forward function applied to real cards.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from tasks.models import WorkOrder
from tasks.services.assignee_resolution import resolve_assignee, resolve_assignees


class _FakeUser:
    """Minimal user-like object for testing the function without the ORM."""

    def __init__(self, pk, username, first_name='', last_name=''):
        self.pk = pk
        self.username = username
        self.first_name = first_name
        self.last_name = last_name


class ResolveAssigneeFunctionTest(TestCase):
    """The matching function in isolation."""

    def setUp(self):
        self.users = [
            _FakeUser(1, 'ada', 'Ada', 'Lovelace'),
            _FakeUser(2, 'grace', 'Grace', 'Hopper'),
            _FakeUser(3, 'aturing', 'Alan', 'Turing'),
        ]

    def test_exact_username_matches(self):
        self.assertEqual(resolve_assignee('ada', self.users), 1)

    def test_case_insensitive_username_matches(self):
        self.assertEqual(resolve_assignee('ADA', self.users), 1)
        self.assertEqual(resolve_assignee('Grace', self.users), 2)

    def test_full_name_matches(self):
        self.assertEqual(resolve_assignee('Ada Lovelace', self.users), 1)
        self.assertEqual(resolve_assignee('alan turing', self.users), 3)

    def test_whitespace_is_normalized(self):
        self.assertEqual(resolve_assignee('  Ada   Lovelace  ', self.users), 1)

    def test_unknown_name_returns_none(self):
        self.assertIsNone(resolve_assignee('Nobody Here', self.users))

    def test_empty_returns_none(self):
        self.assertIsNone(resolve_assignee('', self.users))
        self.assertIsNone(resolve_assignee('   ', self.users))

    def test_exact_username_wins_over_full_name(self):
        """A username collision with someone else's full name resolves to the username."""
        users = [
            _FakeUser(1, 'ada', '', ''),
            # This person's full name normalizes to the string "ada".
            _FakeUser(2, 'other', 'A', 'Da'),
        ]
        # "A Da" -> "a da", not "ada", so no collision here; assert the username path.
        self.assertEqual(resolve_assignee('ada', users), 1)

    def test_ambiguous_full_name_returns_candidate_list(self):
        users = [
            _FakeUser(1, 'jsmith1', 'John', 'Smith'),
            _FakeUser(2, 'jsmith2', 'John', 'Smith'),
        ]
        result = resolve_assignee('John Smith', users)
        self.assertEqual(sorted(result), [1, 2])

    def test_ambiguous_case_insensitive_username_returns_candidate_list(self):
        users = [_FakeUser(1, 'Bob'), _FakeUser(2, 'bob')]
        # 'bob' exact-matches user 2 uniquely, so that is not ambiguous.
        self.assertEqual(resolve_assignee('bob', users), 2)
        # 'BOB' matches neither exactly, then both case-insensitively.
        result = resolve_assignee('BOB', users)
        self.assertEqual(sorted(result), [1, 2])

    def test_blank_names_do_not_match_on_empty_full_name(self):
        """A user with no first/last name must not match an empty-ish query."""
        users = [_FakeUser(1, 'ghost', '', '')]
        self.assertIsNone(resolve_assignee('  ', users))


class ResolveAssigneesReportTest(TestCase):
    """The batch resolver and its report."""

    def setUp(self):
        self.users = [
            _FakeUser(1, 'ada', 'Ada', 'Lovelace'),
            _FakeUser(2, 'jsmith1', 'John', 'Smith'),
            _FakeUser(3, 'jsmith2', 'John', 'Smith'),
        ]

    def test_report_partitions_into_matched_unmatched_ambiguous(self):
        report = resolve_assignees(
            ['ada', 'John Smith', 'Nobody', 'ada', ''], self.users
        )

        self.assertEqual(report.matched, {'ada': 1})
        self.assertEqual(report.unmatched, ['Nobody'])
        self.assertEqual(sorted(report.ambiguous['John Smith']), [2, 3])

    def test_duplicate_names_are_reported_once(self):
        report = resolve_assignees(['ada', 'ada', 'ada'], self.users)
        self.assertEqual(list(report.matched), ['ada'])

    def test_log_lines_mention_unmatched_and_ambiguous(self):
        report = resolve_assignees(['Nobody', 'John Smith'], self.users)
        text = '\n'.join(report.as_log_lines())

        self.assertIn('UNMATCHED', text)
        self.assertIn('Nobody', text)
        self.assertIn('AMBIGUOUS', text)


class _HistoricalApps:
    """Resolve the model names a historical migration knows about.

    Migration 0011 asks for ``KanbanCard`` because that is what the model was
    called when it ran, and a migration must keep describing the state of its
    own moment. This test exercises the backfill against real rows rather than
    against historical state, so it needs today's model returned under
    yesterday's name.
    """

    HISTORICAL_NAMES = {'kanbancard': 'workorder'}

    def get_model(self, app_label, model_name):
        """Return the live model, translating names the migration predates."""
        from django.apps import apps

        key = model_name.lower()
        return apps.get_model(app_label, self.HISTORICAL_NAMES.get(key, key))


class AssigneeBackfillMigrationTest(TestCase):
    """The migration's forward function applied to real work orders."""

    def _forward(self):
        import importlib

        module = importlib.import_module(
            'tasks.migrations.0011_backfill_assigned_to'
        )
        module.backfill_assigned_to(_HistoricalApps(), None)

    def setUp(self):
        User = get_user_model()
        self.ada = User.objects.create_user(
            username='ada', email='ada@example.com', password='pw',
            first_name='Ada', last_name='Lovelace',
        )
        self.smith1 = User.objects.create_user(
            username='jsmith1', email='s1@example.com', password='pw',
            first_name='John', last_name='Smith',
        )
        self.smith2 = User.objects.create_user(
            username='jsmith2', email='s2@example.com', password='pw',
            first_name='John', last_name='Smith',
        )

    def test_matched_card_gets_the_fk(self):
        work_order = WorkOrder.objects.create(
            title='c', status='backlog', priority='low', assignee='ada'
        )

        self._forward()

        work_order.refresh_from_db()
        self.assertEqual(work_order.assigned_to, self.ada)
        # The free-text value is retained for one release.
        self.assertEqual(work_order.assignee, 'ada')

    def test_full_name_match_sets_the_fk(self):
        work_order = WorkOrder.objects.create(
            title='c', status='backlog', priority='low', assignee='Ada Lovelace'
        )

        self._forward()

        work_order.refresh_from_db()
        self.assertEqual(work_order.assigned_to, self.ada)

    def test_ambiguous_name_is_left_unassigned(self):
        work_order = WorkOrder.objects.create(
            title='c', status='backlog', priority='low', assignee='John Smith'
        )

        self._forward()

        work_order.refresh_from_db()
        self.assertIsNone(work_order.assigned_to)
        self.assertEqual(work_order.assignee, 'John Smith')

    def test_unmatched_name_is_left_unassigned(self):
        work_order = WorkOrder.objects.create(
            title='c', status='backlog', priority='low', assignee='Dave (contract)'
        )

        self._forward()

        work_order.refresh_from_db()
        self.assertIsNone(work_order.assigned_to)

    def test_an_existing_fk_is_never_overwritten(self):
        work_order = WorkOrder.objects.create(
            title='c', status='backlog', priority='low',
            assignee='ada', assigned_to=self.smith1,
        )

        self._forward()

        work_order.refresh_from_db()
        # 'ada' would resolve to self.ada, but the card already had a different
        # FK, so the back-fill must not touch it.
        self.assertEqual(work_order.assigned_to, self.smith1)

    def test_is_idempotent(self):
        work_order = WorkOrder.objects.create(
            title='c', status='backlog', priority='low', assignee='ada'
        )

        self._forward()
        self._forward()

        work_order.refresh_from_db()
        self.assertEqual(work_order.assigned_to, self.ada)

    def test_multiple_cards_with_the_same_assignee_all_get_linked(self):
        for index in range(3):
            WorkOrder.objects.create(
                title=f'c{index}', status='backlog', priority='low', assignee='ada'
            )

        self._forward()

        self.assertEqual(
            WorkOrder.objects.filter(assigned_to=self.ada).count(), 3
        )

    def test_blank_assignees_are_ignored(self):
        work_order = WorkOrder.objects.create(
            title='c', status='backlog', priority='low', assignee=''
        )

        self._forward()

        work_order.refresh_from_db()
        self.assertIsNone(work_order.assigned_to)
