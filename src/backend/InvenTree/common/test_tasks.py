"""Tests for tasks in app common."""

from datetime import datetime, timezone
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings

import requests

from common.models import NewsFeedEntry, NotificationEntry
from InvenTree.tasks import offload_task

from . import tasks as common_tasks

NEWS_FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>InvenTree test news</title>
  <id>https://example.test/news.atom</id>
  <updated>2026-10-20T12:00:00+00:00</updated>
  <entry>
    <id>tag:example.test,2026:offline-news</id>
    <title>Offline news fixture</title>
    <link href="/news/offline-news/" />
    <published>2026-10-20T12:00:00+00:00</published>
    <updated>2026-10-20T12:00:00+00:00</updated>
    <author><name>InvenTree Test Author</name></author>
    <summary type="text">An offline feed entry.</summary>
  </entry>
</feed>
"""


class TaskTest(TestCase):
    """Tests for common tasks."""

    def test_delete(self):
        """Test that the task `delete_old_notifications` runs through without errors."""
        # check empty run
        self.assertEqual(NotificationEntry.objects.all().count(), 0)
        offload_task(common_tasks.delete_old_notifications)


@override_settings(USE_TZ=True, INVENTREE_NEWS_URL='https://example.test/news.atom')
class NewsFeedTests(TestCase):
    """Tests for update_news_feed task.

    Tests cover different networking and addressing possibilities.
    """

    def setUp(self):
        """Setup for tests."""
        NewsFeedEntry.objects.all().delete()

    @mock.patch('common.tasks.requests.get')
    def test_valid_url(self, mock_get):
        """Tests that news feed is updated when accessing a valid URL."""
        # Mock only HTTP transport: parse a real Atom document and persist it.
        mock_get.return_value.content = NEWS_FEED
        try:
            common_tasks.update_news_feed()
        except Exception as ex:  # pragma: no cover
            self.fail(f'News feed raised exceptions: {ex}')

        self.assertNotEqual(NewsFeedEntry.objects.all().count(), 0)
        mock_get.assert_called_once_with(settings.INVENTREE_NEWS_URL, timeout=30)

        entry = NewsFeedEntry.objects.get()
        self.assertEqual(entry.feed_id, 'tag:example.test,2026:offline-news')
        self.assertEqual(entry.title, 'Offline news fixture')
        self.assertEqual(
            entry.link, settings.INVENTREE_BASE_URL + '/news/offline-news/'
        )
        self.assertEqual(
            entry.published, datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        )
        self.assertEqual(entry.author, 'InvenTree Test Author')
        self.assertEqual(entry.summary, 'An offline feed entry.')
        self.assertFalse(entry.read)

        common_tasks.update_news_feed()
        self.assertEqual(NewsFeedEntry.objects.count(), 1)
        self.assertEqual(NewsFeedEntry.objects.get().pk, entry.pk)
        self.assertEqual(mock_get.call_count, 2)

    @mock.patch(
        'common.tasks.requests.get',
        side_effect=requests.ConnectionError('Offline test endpoint'),
    )
    def test_connection_error(self, mock_get):
        """Test connecting to an unavailable endpoint.

        This also emulates calling the endpoint behind a blocking proxy.
        """
        with self.assertLogs('inventree', level='WARNING'):
            common_tasks.update_news_feed()

        mock_get.assert_called_once_with(settings.INVENTREE_NEWS_URL, timeout=30)
        self.assertEqual(NewsFeedEntry.objects.all().count(), 0)

    @mock.patch('common.tasks.requests.get')
    def test_unset_url(self, mock_get):
        """Test that no call is made to news feed if URL setting is invalid."""
        for url in ['', 0, None]:
            with self.subTest(url=url), override_settings(INVENTREE_NEWS_URL=url):
                self.assertTrue(
                    offload_task(common_tasks.update_news_feed)
                )  # Task considered complete
                self.assertEqual(
                    NewsFeedEntry.objects.all().count(), 0
                )  # No Feed entries created

        mock_get.assert_not_called()
