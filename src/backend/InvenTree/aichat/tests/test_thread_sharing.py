"""S32b (B6): explicit read-only thread grants widen reads and nothing else."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from aichat.models import ChatThreadGrant, MessageRole
from aichat.services import InvalidBoundary, ThreadNotFound, ThreadRepository


@override_settings(FEATURE_THREAD_SHARING=True)
class ThreadSharingTests(TestCase):
    """Grant lifecycle, the read widening, and the write paths that stay shut."""

    def setUp(self) -> None:
        """One owner with a thread, one grantee, one outsider scope."""
        users = get_user_model().objects
        self.owner = users.create_user(username='share-owner')
        self.grantee = users.create_user(username='share-grantee')
        from aichat.tests.memory_scope_fixtures import grant_shared_fixture_client

        grant_shared_fixture_client(self, self.owner, self.grantee)
        self.owner_repo = ThreadRepository(self.owner.pk, 'site:main')
        self.grantee_repo = ThreadRepository(self.grantee.pk, 'site:main')
        self.thread, _ = self.owner_repo.get_or_create(title='Pump notes')
        self.owner_repo.append(
            self.thread.pk, role=MessageRole.USER, content='what about the pump?'
        )

    def test_default_is_owner_only(self) -> None:
        """Without a grant the grantee sees nothing, list included."""
        with self.assertRaises(ThreadNotFound):
            self.grantee_repo.get_readable(self.thread.pk)
        self.assertEqual(self.grantee_repo.list_shared(), [])
        self.assertEqual(self.grantee_repo.list(), [])

    def test_revoking_client_access_hides_existing_grant(self):
        """Thread grants do not outlive the recipient's client authorization."""
        from assets.models import ClientScopeGrant

        self.owner_repo.share(self.thread.pk, grantee_id=self.grantee.pk)
        ClientScopeGrant.objects.filter(user=self.grantee).delete()
        with self.assertRaises(ThreadNotFound):
            self.grantee_repo.get_readable(self.thread.pk)
        self.assertEqual(self.grantee_repo.list_shared(), [])

    def test_client_superset_and_once_confirmed_explicit_scope(self):
        """A B-only grantee cannot read an A+B thread or a re-scoped thread."""
        from ai.core.analysis.scope import AnalysisScope, scope_to_payload
        from aichat.models import ChatThread
        from assets.models import AssetMachine, Client, ClientScopeGrant

        client_b = Client.objects.create(code='share-client-b', name='Share client B')
        machine = AssetMachine.objects.create(
            name='Share fixture machine', client=client_b
        )
        ClientScopeGrant.objects.create(user=self.owner, client=client_b)
        ClientScopeGrant.objects.filter(user=self.grantee).delete()
        ClientScopeGrant.objects.create(user=self.grantee, client=client_b)
        with self.assertRaises(InvalidBoundary):
            self.owner_repo.share(self.thread.pk, grantee_id=self.grantee.pk)
        self.assertFalse(ChatThreadGrant.objects.filter(thread=self.thread).exists())
        ChatThread.objects.filter(pk=self.thread.pk).update(
            analysis_scope=scope_to_payload(
                AnalysisScope(mode='explicit_assets', machine_ids=(machine.pk,))
            ),
            analysis_scope_version=1,
        )
        self.owner_repo.share(self.thread.pk, grantee_id=self.grantee.pk)
        self.grantee_repo.get_readable(self.thread.pk)
        ChatThread.objects.filter(pk=self.thread.pk).update(analysis_scope_version=2)
        with self.assertRaises(ThreadNotFound):
            self.grantee_repo.get_readable(self.thread.pk)

    def test_memory_informed_client_union_cannot_be_lost_on_scope_change(self):
        """Historical memory provenance contributes to the whole-thread boundary."""
        from aichat.models import ChatMessage

        self.owner_repo.share(self.thread.pk, grantee_id=self.grantee.pk)
        ChatMessage.objects.create(
            thread=self.thread,
            sequence=2,
            role='assistant',
            content='Fixture response',
            metadata={'memory_informed_clients': ['formerly-authorized-client']},
        )
        with self.assertRaises(ThreadNotFound):
            self.grantee_repo.get_readable(self.thread.pk)
        self.assertEqual(self.grantee_repo.list_shared(), [])

    def test_grant_confers_read_and_only_read(self) -> None:
        """A granted thread is readable; every write path still refuses."""
        self.owner_repo.share(self.thread.pk, grantee_id=self.grantee.pk)

        thread, shared = self.grantee_repo.get_readable(self.thread.pk)
        self.assertTrue(shared)
        self.assertEqual(thread.pk, self.thread.pk)
        contents = [
            message.content
            for message in self.grantee_repo.readable_messages(self.thread.pk)
        ]
        self.assertIn('what about the pump?', contents)
        self.assertEqual(
            [row.pk for row in self.grantee_repo.list_shared()], [self.thread.pk]
        )
        # The owned list stays owner-only; a grant never mingles ownership.
        self.assertEqual(self.grantee_repo.list(), [])

        with self.assertRaises(ThreadNotFound):
            self.grantee_repo.rename(self.thread.pk, 'hijacked')
        with self.assertRaises(ThreadNotFound):
            self.grantee_repo.delete(self.thread.pk)
        with self.assertRaises(ThreadNotFound):
            self.grantee_repo.append(
                self.thread.pk, role=MessageRole.USER, content='write attempt'
            )
        # The owner's read reports not-shared.
        _, owner_shared = self.owner_repo.get_readable(self.thread.pk)
        self.assertFalse(owner_shared)

    def test_share_is_idempotent_and_owner_only(self) -> None:
        """Re-sharing reuses the active grant; a non-owner cannot share."""
        first = self.owner_repo.share(self.thread.pk, grantee_id=self.grantee.pk)
        second = self.owner_repo.share(self.thread.pk, grantee_id=self.grantee.pk)
        self.assertEqual(first.pk, second.pk)
        with self.assertRaises(ThreadNotFound):
            self.grantee_repo.share(self.thread.pk, grantee_id=self.owner.pk)
        with self.assertRaises(InvalidBoundary):
            self.owner_repo.share(self.thread.pk, grantee_id=self.owner.pk)

    def test_revoke_stops_reads_and_keeps_the_audit_row(self) -> None:
        """Revocation is a stamp, never a delete."""
        self.owner_repo.share(self.thread.pk, grantee_id=self.grantee.pk)
        revoked = self.owner_repo.revoke_share(
            self.thread.pk, grantee_id=self.grantee.pk
        )
        self.assertEqual(revoked, 1)
        with self.assertRaises(ThreadNotFound):
            self.grantee_repo.get_readable(self.thread.pk)
        self.assertEqual(self.grantee_repo.list_shared(), [])
        row = ChatThreadGrant.objects.get(thread=self.thread)
        self.assertIsNotNone(row.revoked_at)

    def test_expired_grant_confers_nothing(self) -> None:
        """An expires_at in the past behaves like a revocation."""
        self.owner_repo.share(
            self.thread.pk,
            grantee_id=self.grantee.pk,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        with self.assertRaises(ThreadNotFound):
            self.grantee_repo.get_readable(self.thread.pk)

    def test_grant_never_crosses_the_scope_boundary(self) -> None:
        """A grant is only readable from the scope the thread lives in."""
        self.owner_repo.share(self.thread.pk, grantee_id=self.grantee.pk)
        foreign_scope = ThreadRepository(self.grantee.pk, 'site:other')
        with self.assertRaises(ThreadNotFound):
            foreign_scope.get_readable(self.thread.pk)
        self.assertEqual(foreign_scope.list_shared(), [])

    @override_settings(FEATURE_THREAD_SHARING=False)
    def test_flag_off_is_a_kill_switch(self) -> None:
        """With the feature dark, even an existing grant confers nothing."""
        ChatThreadGrant.objects.create(
            thread=self.thread, grantee=self.grantee, granted_by=self.owner
        )
        with self.assertRaises(ThreadNotFound):
            self.grantee_repo.get_readable(self.thread.pk)
        self.assertEqual(self.grantee_repo.list_shared(), [])
        with self.assertRaises(InvalidBoundary):
            self.owner_repo.share(self.thread.pk, grantee_id=self.grantee.pk)
