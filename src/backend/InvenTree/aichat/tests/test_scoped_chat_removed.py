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


from django.db import connection  # noqa: E402
from django.db.migrations.executor import MigrationExecutor  # noqa: E402
from django.test import TransactionTestCase, tag  # noqa: E402


@tag('migration_test')
class DropScopedChatMigrationTests(TransactionTestCase):
    """The destructive S14(c) drop is structurally zero-row-gated."""

    def _executor(self) -> MigrationExecutor:
        connection.close()
        return MigrationExecutor(connection)

    def test_forward_succeeds_on_zero_rows(self) -> None:
        """With an empty rail the drop removes all four tables."""
        self._executor().migrate([('aichat', '0013_retrievalmiss')])
        tables = set(connection.introspection.table_names())
        self.assertIn('aichat_scopedconversation', tables)

        self._executor().migrate([('aichat', '0014_drop_scoped_chat')])
        tables = set(connection.introspection.table_names())
        for table in (
            'aichat_scopedconversation',
            'aichat_scopedconversationgrant',
            'aichat_chatcitation',
            'aichat_chattoolinvocation',
        ):
            self.assertNotIn(table, tables)

    def test_forward_aborts_on_a_live_scoped_row(self) -> None:
        """The migration itself refuses when rows exist — the gate is code."""
        executor = self._executor()
        executor.migrate([('aichat', '0013_retrievalmiss')])
        apps = executor.loader.project_state(
            ('aichat', '0013_retrievalmiss')
        ).apps
        user = apps.get_model('auth', 'User').objects.create(
            username='scoped-abort-probe'
        )
        apps.get_model('aichat', 'ScopedConversation').objects.create(
            owner_id=user.pk,
            context_type='work_order',
            object_id='1',
            scope_key='site:test',
            scope_hash='0' * 64,
            ai_thread_id='scoped_thread_probe',
        )

        with self.assertRaisesMessage(RuntimeError, 'Refusing to drop'):
            self._executor().migrate([('aichat', '0014_drop_scoped_chat')])

        # Clean up so the post-test migrate-forward succeeds.
        apps.get_model('aichat', 'ScopedConversation').objects.all().delete()
        self._executor().migrate([('aichat', '0014_drop_scoped_chat')])
