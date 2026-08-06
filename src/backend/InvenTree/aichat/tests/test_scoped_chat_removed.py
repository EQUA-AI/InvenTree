"""S14(b): the scoped-chat server rail is gone and stays gone.

The parallel scoped-access rail (context resolution, scoped conversations,
per-call tool invocation) was never lit in production and duplicated main-rail
authorization. These pins fail the moment any of its endpoints or services
reappear. The models are dropped separately in the S14(c) migration.
"""

import importlib.util

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, reverse


class ScopedChatRemovalTests(TestCase):
    """Absence pins for the removed scoped-chat rail."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username='scoped-removal', password='x'
        )

    def test_scoped_service_modules_are_gone(self):
        """The rail's service modules cannot be imported."""
        for module in (
            'aichat.services.context',
            'aichat.services.conversations',
            'aichat.services.tools',
            'aichat.services.controlled_document_selection',
        ):
            self.assertIsNone(importlib.util.find_spec(module), module)

    def test_scoped_routes_are_unroutable(self):
        """No URL name for the rail resolves any more."""
        for name in (
            'aichat:context-resolve',
            'aichat:conversation-list',
            'aichat:conversation-detail',
            'aichat:conversation-citations',
            'aichat:conversation-tools',
            'aichat:conversation-tool-invoke',
        ):
            with self.assertRaises(NoReverseMatch, msg=name):
                reverse(name, kwargs={})

    def test_scoped_endpoints_404(self):
        """The old paths are dead even for an authenticated session."""
        self.client.force_login(self.user)
        for path in (
            '/api/aichat/context/resolve/',
            '/api/aichat/conversations/',
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, path)

    def test_surviving_rail_still_routes(self):
        """Proposals and feedback — the main rail — are untouched."""
        self.assertTrue(reverse('aichat:proposal-list'))
        self.assertTrue(reverse('aichat:message-feedback'))
