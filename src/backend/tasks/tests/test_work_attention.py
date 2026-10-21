"""R6 UI10 "Needs attention": a named rule over stored Work facts, explained per row."""

from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from assets.models import AssetMachine, Client, ClientScopeGrant
from company.models import Company
from tasks.attention import NEEDS_ATTENTION_RULE
from tasks.models import WorkOrder, WorkOrderLifecycle
from users.models import RuleSet

LIST = '/api/kanban/work-orders/list/'
BOARD = '/api/kanban/work-orders/'
TODAY = date(2026, 10, 19)


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
@mock.patch('django.utils.timezone.localdate', return_value=TODAY)
class NeedsAttentionTests(TestCase):
    """Normal reader, stored role, explicit client grant and real Work rows."""

    def setUp(self):
        """One qualifying Work per criterion, plus near misses and other scopes."""
        self.reader = get_user_model().objects.create_user(
            username='attention-reader', password='synthetic-only'
        )
        self.crew = get_user_model().objects.create_user(username='attention-crew')
        group = Group.objects.create(name='Attention readers')
        RuleSet.objects.update_or_create(
            name='work_order', group=group, defaults={'can_view': True}
        )
        self.reader.groups.add(group)
        tenant = Client.objects.create(name='Attention scope', code='attention')
        self.grant = ClientScopeGrant.objects.create(user=self.reader, client=tenant)
        machine = AssetMachine.objects.create(name='Attention pump', client=tenant)

        def work(title, *, priority='medium', lifecycle='planned', **extra):
            extra.setdefault('assigned_to', self.crew)
            return WorkOrder.objects.create(
                title=title,
                machine=machine,
                priority=priority,
                lifecycle_status=lifecycle,
                **extra,
            )

        late = TODAY - timedelta(days=3)
        soon = TODAY + timedelta(days=3)
        self.high = work('High', priority='high', due_date=soon)
        self.high_late_unowned = work(
            'High late unowned', priority='high', due_date=late, assigned_to=None
        )
        self.high_undated = work('High undated', priority='high')
        self.overdue = work('Overdue', lifecycle='ready', due_date=late)
        self.on_hold = work('On hold', priority='low', lifecycle='on_hold')
        self.unowned = work('No owner', assigned_to=None)
        # Near misses: each is one fact away from qualifying.
        work('Due today', due_date=TODAY)
        work('Owner named in text', assigned_to=None, assignee='Crew B')
        work('Completed high', priority='high', lifecycle=WorkOrderLifecycle.COMPLETED)
        work('Draft high', priority='high', lifecycle='draft')
        work('Archived high', priority='high', is_active=False)
        work(
            'Shared customer high',
            priority='high',
            customer=Company.objects.create(name='Elsewhere', is_customer=True),
        )
        self.api = APIClient()
        self.assertTrue(
            self.api.login(username=self.reader.username, password='synthetic-only')
        )

    def get(self, url=LIST, **params):
        """Read as the Work list does."""
        response = self.api.get(url, params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_rule_is_versioned(self, _today):
        """The rule has a name the list and the records can cite."""
        self.assertEqual(NEEDS_ATTENTION_RULE, 'needs_attention.v1')

    def test_only_open_scoped_work_meeting_a_criterion_qualifies(self, _today):
        """Each row says why; near misses and other scopes stay out."""
        page = self.get(attention='true')
        reasons = {row['title']: row['attention_reasons'] for row in page['results']}
        self.assertEqual(
            reasons,
            {
                'High': ['high_priority'],
                'Shared customer high': ['high_priority'],
                'High late unowned': ['high_priority', 'overdue', 'no_owner'],
                'High undated': ['high_priority'],
                'Overdue': ['overdue'],
                'On hold': ['on_hold'],
                'No owner': ['no_owner'],
            },
        )
        self.assertEqual(page['count'], 7)

    def test_order_is_priority_then_earliest_due_then_newest(self, _today):
        """No score: native priority, then the due window, undated last."""
        titles = [row['title'] for row in self.get(attention='true')['results']]
        self.assertEqual(
            titles,
            [
                'High late unowned',
                'High',
                'Shared customer high',
                'High undated',
                'Overdue',
                'No owner',
                'On hold',
            ],
        )

    def test_explicit_ordering_is_respected(self, _today):
        """A caller who asks for an order gets it, within the same rows."""
        titles = [
            row['title']
            for row in self.get(attention='true', ordering='priority')['results']
        ]
        # The native text ordering puts "low" before "medium"; the attention
        # order puts it last.
        self.assertLess(titles.index('On hold'), titles.index('Overdue'))
        self.assertEqual(len(titles), 7)

    def test_filters_and_search_narrow_within_the_rule(self, _today):
        """Native filters still apply before counting and paging."""
        high = self.get(attention='true', priority='high')
        self.assertEqual(high['count'], 4)
        searched = self.get(attention='true', search='hold')
        self.assertEqual([row['title'] for row in searched['results']], ['On hold'])

    def test_other_scopes_have_no_reasons_and_the_board_keeps_its_shape(self, _today):
        """Reasons are null outside the scope; the Board gains no field."""
        everything = self.get()
        self.assertGreater(everything['count'], 6)
        self.assertTrue(
            all(row['attention_reasons'] is None for row in everything['results'])
        )
        board = self.get(BOARD)
        self.assertIsInstance(board, list)
        self.assertNotIn('attention_reasons', board[0])

    def test_only_true_or_false_are_accepted(self, _today):
        """An unexpected value is refused, not treated as either scope."""
        for value in ['1', 'yes', 'TRUE']:
            with self.subTest(value=value):
                response = self.api.get(LIST, {'attention': value})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.get(attention='false')['count'], self.get()['count'])

    def test_overdue_follows_the_site_date(self, today):
        """Moving the site date moves what is overdue, nothing else."""
        today.return_value = TODAY + timedelta(days=5)
        titles = {
            row['title']
            for row in self.get(attention='true')['results']
            if 'overdue' in row['attention_reasons']
        }
        self.assertEqual(titles, {'High late unowned', 'Overdue', 'High', 'Due today'})

    def test_revoked_client_grant_retains_shared_attention_work(self, _today):
        """The rule never widens what the reader may see."""
        self.grant.delete()
        self.assertEqual([row['title'] for row in self.get(attention='true')['results']], ['Shared customer high'])
