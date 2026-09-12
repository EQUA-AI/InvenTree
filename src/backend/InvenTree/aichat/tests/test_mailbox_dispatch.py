"""Mailbox authority, frozen content and failure recovery without live mail."""

from email import policy
from email.parser import BytesParser
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from cryptography.fernet import Fernet
from rest_framework.test import APIClient

from ai.core.integrations.email.contracts import (
    AccountConfig,
    MailboxError,
    SendObservation,
)
from ai.core.integrations.email.recording import RecordingProvider
from aichat.models import ConnectedMailbox, MailDraft, MailReceipt
from aichat.services.email.accounts import public_account, save_account
from aichat.services.email.dispatch import dispatch_pending, reconcile
from aichat.services.email.drafts import create_draft
from approvals import services
from approvals.models import ApprovalExecution


@override_settings(
    AGENT_EMAIL_ENABLED=True, AGENT_EMAIL_MESSAGE_ID_DOMAIN='mail.example.test'
)
class MailboxDispatchTests(TestCase):
    """Exercise the real approval service with a recording transport."""

    def setUp(self):
        """Create two isolated identities; no external provider is configured."""
        from django.contrib.contenttypes.models import ContentType

        ContentType.objects.clear_cache()
        self.user = get_user_model().objects.create_superuser(
            'mailbox-admin', 'admin@example.test', 'test'
        )
        self.outsider = get_user_model().objects.create_user('mailbox-outsider')
        self.account = ConnectedMailbox.objects.create(
            name='Recording',
            provider='recording',
            address='sender@example.test',
            owner=self.user,
            enabled=True,
            send_enabled=True,
            receive_enabled=True,
            verified_send_at=timezone.now(),
            verified_receive_at=timezone.now(),
            recipient_allowlist=['@example.test'],
        )
        self.provider = RecordingProvider(
            AccountConfig(str(self.account.pk), 'recording', self.account.address)
        )
        self.enterContext(
            patch(
                'aichat.services.email.dispatch.provider_for',
                return_value=self.provider,
            )
        )
        self.enterContext(patch('aichat.services.email.dispatch.publish'))
        self.enterContext(
            patch.dict(
                'os.environ', {'AIMMS_EMAIL_RECIPIENT_ALLOWLIST': '@example.test'}
            )
        )
        self.data = {
            'to': ['recipient@example.test'],
            'bcc': ['hidden@example.test'],
            'subject': 'Recording only',
            'body': 'Hello',
        }

    def approve(self):
        """Complete normal screen review before creating a pending intent."""
        approval = create_draft(self.user, self.account.pk, self.data, 'request-1')
        services.open_approval(approval.pk, actor=self.user)
        from approvals.review_evidence import required_sections
        from approvals.review_sections import compute_review_hash

        services.confirm_viewed(
            approval.pk,
            actor=self.user,
            data={
                'revision': 0,
                'review_hash': compute_review_hash(approval),
                'sections': required_sections(approval),
            },
        )
        services.approve(approval.pk, actor=self.user)
        return approval, ApprovalExecution.objects.get(approval=approval)

    def test_approval_is_durable_and_duplicate_workers_do_not_resend(self):
        """Acceptance without provider IDs still has a real local receipt."""
        approval, operation = self.approve()
        self.assertEqual(operation.state, 'pending_dispatch')
        self.assertEqual(self.provider.calls, [])
        dispatch_pending(operation.pk)
        dispatch_pending(operation.pk)
        services.approve(approval.pk, actor=self.user)
        self.assertEqual(len(self.provider.calls), 1)
        operation.refresh_from_db()
        self.assertEqual(operation.state, 'succeeded')
        self.assertTrue(operation.result['effect_ref'].startswith('email-receipt:'))
        self.assertEqual(MailReceipt.objects.count(), 1)
        message = BytesParser(policy=policy.default).parsebytes(
            bytes(approval.email_draft.raw)
        )
        self.assertIsNone(message['Bcc'])
        self.assertIn('hidden@example.test', approval.email_draft.envelope)

    def test_unknown_is_never_retried_by_worker_or_reconciliation(self):
        """A lost transport response retains uncertainty and never sends twice."""
        self.provider.observations.append(TimeoutError())
        approval, operation = self.approve()
        dispatch_pending(operation.pk)
        operation.refresh_from_db()
        self.assertEqual(operation.state, 'unknown')
        reconcile(approval, operation)
        dispatch_pending(operation.pk)
        self.assertEqual(len(self.provider.calls), 1)

    def test_mutation_and_account_disable_are_checked_at_claim(self):
        """Changes after approval cannot authorize different bytes."""
        approval, operation = self.approve()
        MailDraft.objects.filter(approval=approval).update(raw=b'different bytes')
        dispatch_pending(operation.pk)
        operation.refresh_from_db()
        self.assertEqual(operation.state, 'failed_before_effect')
        self.assertEqual(self.provider.calls, [])

    def test_reviewer_revocation_before_claim(self):
        """Cached reviewer objects cannot retain send authority."""
        _, operation = self.approve()
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        dispatch_pending(operation.pk)
        operation.refresh_from_db()
        self.assertEqual(operation.state, 'failed_before_effect')
        self.assertEqual(self.provider.calls, [])

    def test_partial_evidence_cannot_be_downgraded(self):
        """A later empty lookup cannot erase known accepted recipients."""
        self.provider.observations.append(
            SendObservation('partial', ('accepted', 'rejected'), 'transport_response')
        )
        approval, operation = self.approve()
        dispatch_pending(operation.pk)
        operation.refresh_from_db()
        reconcile(approval, operation)
        operation.refresh_from_db()
        self.assertEqual(operation.state, 'partial')
        self.assertEqual(
            operation.result['payload']['recipients'], ['accepted', 'rejected']
        )

    def test_idempotency_rejects_changed_content(self):
        """A request key identifies one immutable draft."""
        first = create_draft(self.user, self.account.pk, self.data, 'request-1')
        self.assertEqual(
            create_draft(self.user, self.account.pk, self.data, 'request-1').pk,
            first.pk,
        )
        with self.assertRaisesMessage(MailboxError, 'idempotency_conflict'):
            create_draft(
                self.user,
                self.account.pk,
                {**self.data, 'body': 'Changed'},
                'request-1',
            )

    def test_private_approval_and_history_are_not_visible(self):
        """Global authentication alone cannot read email derivatives."""
        approval = create_draft(self.user, self.account.pk, self.data, 'request-1')
        client = APIClient()
        client.force_authenticate(self.outsider)
        self.assertEqual(client.get(f'/api/approvals/{approval.pk}/').status_code, 404)
        self.assertEqual(
            client.get(f'/api/aichat/email/accounts/{self.account.pk}/').status_code,
            404,
        )
        self.assertEqual(client.get('/api/aichat/email/accounts/').data['results'], [])

    def test_configuration_masks_and_encrypts_credentials(self):
        """An admin response never returns plaintext or ciphertext secrets."""
        with override_settings(
            AGENT_EMAIL_CREDENTIAL_KEYS=[Fernet.generate_key().decode()]
        ):
            account = save_account(
                self.user,
                {
                    'name': 'Another',
                    'provider': 'recording',
                    'address': 'another@example.test',
                    'credentials': {'password': 'recording-only'},
                },
            )
        self.assertNotIn('recording-only', account.encrypted_credentials)
        self.assertNotIn('credentials', str(public_account(account, admin=True)))

    def test_stale_review_cannot_approve_mutated_payload(self):
        """Mailbox reviews remain revision-bound even with the legacy flag off."""
        from approvals.review_evidence import required_sections
        from approvals.review_sections import compute_review_hash

        approval = create_draft(self.user, self.account.pk, self.data, 'stale-review')
        services.open_approval(approval.pk, actor=self.user)
        services.confirm_viewed(
            approval.pk,
            actor=self.user,
            data={
                'revision': 0,
                'review_hash': compute_review_hash(approval),
                'sections': required_sections(approval),
            },
        )
        approval.payload['body'] = 'Changed after review'
        approval.save(update_fields=['payload'])
        with self.assertRaises(services.ApprovalServiceError):
            services.approve(approval.pk, actor=self.user)
        self.assertFalse(ApprovalExecution.objects.exists())
        self.assertEqual(self.provider.calls, [])

    def test_receipt_storage_failure_leaves_claim_non_replayable(self):
        """A crash after acceptance cannot turn a consumed claim back into pending."""
        _, operation = self.approve()
        with (
            patch(
                'aichat.services.email.dispatch.persist',
                side_effect=RuntimeError('recording crash'),
            ),
            self.assertRaises(RuntimeError),
        ):
            dispatch_pending(operation.pk)
        operation.refresh_from_db()
        self.assertEqual(operation.state, 'submitting')
        dispatch_pending(operation.pk)
        self.assertEqual(len(self.provider.calls), 1)

    def test_global_pause_preserves_unclaimed_intent(self):
        """Pausing cannot consume a pending operation or invoke legacy sending."""
        from ai.core.integrations.email.commands import send_message

        _, operation = self.approve()
        with override_settings(AGENT_EMAIL_SEND_PAUSED=True):
            dispatch_pending(operation.pk)
            operation.refresh_from_db()
            self.assertEqual(operation.state, 'pending_dispatch')
            self.assertEqual(self.provider.calls, [])
            with override_settings(AGENT_EMAIL_ENABLED=False):
                result = send_message(
                    self.data, actor=self.user, operation_id='paused-legacy'
                )
                self.assertEqual(result['outcome'], 'failed_before_effect')

    def test_global_reviewer_needs_live_group_mailbox_grant(self):
        """Global review permission never replaces the account grant at claim."""
        from django.contrib.auth.models import Group, Permission

        from aichat.models import MailboxGrant
        from approvals.review_evidence import required_sections
        from approvals.review_sections import compute_review_hash

        group = Group.objects.create(name='mailbox-reviewers')
        group.permissions.add(
            Permission.objects.get(
                content_type__app_label='users', codename='view_email'
            ),
            Permission.objects.get(
                content_type__app_label='users', codename='send_email'
            ),
            Permission.objects.get(
                content_type__app_label='approvals', codename='review'
            ),
        )
        self.outsider.groups.add(group)
        approval = create_draft(self.user, self.account.pk, self.data, 'group-review')
        client = APIClient()
        client.force_authenticate(self.outsider)
        self.assertEqual(client.get(f'/api/approvals/{approval.pk}/').status_code, 404)
        with self.assertRaises(services.ApprovalServiceError):
            services.open_approval(approval.pk, actor=self.outsider)
        MailboxGrant.objects.create(
            account=self.account, group=group, can_read=True, can_send=True
        )
        self.assertEqual(client.get(f'/api/approvals/{approval.pk}/').status_code, 200)
        services.open_approval(approval.pk, actor=self.outsider)
        services.confirm_viewed(
            approval.pk,
            actor=self.outsider,
            data={
                'revision': 0,
                'review_hash': compute_review_hash(approval),
                'sections': required_sections(approval),
            },
        )
        services.approve(approval.pk, actor=self.outsider)
        operation = ApprovalExecution.objects.get(approval=approval)
        self.outsider.groups.remove(group)
        dispatch_pending(operation.pk)
        operation.refresh_from_db()
        self.assertEqual(operation.state, 'failed_before_effect')
        self.assertEqual(self.provider.calls, [])
