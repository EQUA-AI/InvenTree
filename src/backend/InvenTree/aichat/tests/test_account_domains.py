"""Owner-isolated local credential cleanup; no provider or mail is contacted."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from aichat.email_models import ConnectedMailbox, MailboxGrant
from aichat.models import RetrievalMiss
from aichat.services import account_domains
from aichat.services.retrieval_misses import record_search
from assets.models import Client, ClientScopeGrant


class AccountDomainTests(TestCase):
    """Shared account content remains; erased actor authority does not."""

    def setUp(self):
        """Independent owners and a grant into the other owner's mailbox."""
        users = get_user_model().objects
        self.owner = users.create_user(username='domain-erasure-owner', is_active=False)
        self.other = users.create_user(username='domain-erasure-other')
        self.owned = ConnectedMailbox.objects.create(
            owner=self.owner,
            name='fixture',
            address='fixture@example.invalid',
            provider='recording',
            encrypted_credentials='opaque-fixture',
            enabled=True,
            receive_enabled=True,
        )
        self.shared = ConnectedMailbox.objects.create(
            owner=self.other,
            name='shared',
            address='shared@example.invalid',
            provider='recording',
            encrypted_credentials='other-fixture',
            enabled=True,
        )
        MailboxGrant.objects.create(account=self.shared, user=self.owner, can_read=True)
        client = Client.objects.create(name='Fixture', code='domain-fixture')
        ClientScopeGrant.objects.create(client=client, user=self.owner)

    def test_disconnect_and_grant_cleanup_preserve_other_owners(self):
        """Cleanup is local, idempotent and does not delete shared correspondence."""
        result = account_domains.purge(self.owner.pk)
        self.assertEqual(result['status'], 'purged')
        self.assertFalse(result['shared_correspondence_erased'])
        self.owned.refresh_from_db()
        self.shared.refresh_from_db()
        self.assertEqual(self.owned.encrypted_credentials, '')
        self.assertFalse(self.owned.enabled)
        self.assertEqual(self.owned.binding_version, 2)
        self.assertTrue(self.shared.enabled)
        self.assertEqual(self.shared.encrypted_credentials, 'other-fixture')
        self.assertEqual(
            account_domains.purge(self.owner.pk)['processed']['mailboxes_disconnected'],
            0,
        )

    def test_active_owner_refuses_and_stale_query_writer_cannot_repopulate(self):
        """Deactivation is required; a stale caller object cannot bypass it."""
        with self.assertRaises(ValueError):
            account_domains.purge(self.other.pk)
        self.owner.is_active = True
        record_search(
            user=self.owner,
            query='PRIVATE',
            hit_count=0,
            top_score=None,
            machine_filter='',
            document_class=None,
            scope_key='fixture',
        )
        self.assertFalse(RetrievalMiss.objects.filter(user=self.owner).exists())
