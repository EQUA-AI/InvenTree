"""S20 A8: ledger-backed thread search stays inside the owner boundary."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from aichat.models import MessageRole
from aichat.services.threads import ThreadRepository


class ThreadSearchTests(TestCase):
    """Search is built on the boundary queryset, never beside it."""

    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.owner = users.create_user(username='search-owner', password='x')
        cls.other = users.create_user(username='search-other', password='x')

        cls.repository = ThreadRepository(cls.owner.pk, 'site:main')
        cls.pump_thread, _ = cls.repository.get_or_create(
            title='Influent pump vibration'
        )
        cls.valve_thread, _ = cls.repository.get_or_create(title='Valve stems')
        cls.repository.append(
            cls.valve_thread.pk,
            role=MessageRole.USER,
            content='Where is the butterfly valve isolation procedure?',
        )

        other_repository = ThreadRepository(cls.other.pk, 'site:main')
        cls.foreign_thread, _ = other_repository.get_or_create(
            title='Influent pump rebuild notes'
        )

    def test_title_and_content_match(self):
        """Both the title index and message bodies answer a query."""
        by_title = self.repository.search('influent pump')
        self.assertEqual([thread.pk for thread in by_title], [self.pump_thread.pk])

        by_content = self.repository.search('butterfly valve')
        self.assertEqual([thread.pk for thread in by_content], [self.valve_thread.pk])

    def test_owner_boundary_holds(self):
        """User B's threads never match user A's query — and vice versa."""
        results = self.repository.search('influent pump')
        self.assertNotIn(self.foreign_thread.pk, [thread.pk for thread in results])

        other_results = ThreadRepository(self.other.pk, 'site:main').search(
            'influent pump'
        )
        self.assertEqual(
            [thread.pk for thread in other_results], [self.foreign_thread.pk]
        )

    def test_blank_query_falls_back_to_list(self):
        results = self.repository.search('   ')
        self.assertEqual(
            {thread.pk for thread in results},
            {self.pump_thread.pk, self.valve_thread.pk},
        )

    def test_multiple_matching_messages_yield_one_row(self):
        """distinct(): a thread with N matching messages appears once."""
        self.repository.append(
            self.valve_thread.pk,
            role=MessageRole.ASSISTANT,
            content='The butterfly valve procedure is in section 4.',
        )
        results = self.repository.search('butterfly valve')
        self.assertEqual([thread.pk for thread in results], [self.valve_thread.pk])
