"""Real mail command exercised only through a recording Gmail provider."""

import base64
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth.models import Group

from ai.core.integrations.email import commands

from . import services
from .execution import reconcile_execution
from .executors import EmailExecutor, registry
from .models import ActionType, ApprovalExecution, ApprovalStatus, ExecutedEffect
from .tests import ApprovalTestBase


class EmailApprovalTests(ApprovalTestBase):
    """Send authority, complete receipts and no-resend recovery."""

    def setUp(self):
        """Create isolated review authority and a provider which cannot send."""
        super().setUp()
        self.enterContext(
            patch.dict('os.environ', {'AIMMS_EMAIL_RECIPIENT_ALLOWLIST': '@equa.work'})
        )
        self.user.groups.add(Group.objects.create(name='aimms.email.send'))
        self.approval = self._create_approval_obj(
            action_type=ActionType.EMAIL,
            payload={
                'to': 'lokesh@equa.work',
                'subject': 'VOICE-TEST recording only',
                'body': 'Please reply to this test.\n',
            },
        )
        services.open_approval(self.approval.pk, actor=self.user)
        services.confirm_viewed(self.approval.pk, actor=self.user)
        self.enterContext(registry.replace_for_tests(EmailExecutor()))
        self.client_stub = Mock(email='aimms@equa.work')
        self.messages = self.client_stub._get_service.return_value.users.return_value.messages.return_value
        self.messages.send.return_value.execute.return_value = {
            'id': 'gmail-recording-1',
            'threadId': 'thread-recording-1',
        }
        self.enterContext(
            patch.object(commands, 'get_gmail_client', return_value=self.client_stub)
        )

    def _sent_message(self):
        raw = self.messages.send.call_args.kwargs['body']['raw']
        return raw, BytesParser(policy=policy.default).parsebytes(
            base64.urlsafe_b64decode(raw)
        )

    def test_complete_envelope_real_receipt_and_replay(self):
        """Actual canonical MIME is dispatched only once with its durable key."""
        self.approval.payload.update(cc=['copy@equa.work'], bcc=['audit@equa.work'])
        self.approval.save(update_fields=['payload'])

        def receive(**kwargs):
            self.assertEqual(kwargs, {'num_retries': 0})
            self.assertEqual(
                ApprovalExecution.objects.get(approval=self.approval).state,
                'submitting',
            )
            return {'id': 'gmail-recording-1', 'threadId': 'thread-recording-1'}

        self.messages.send.return_value.execute.side_effect = receive
        result = services.approve(self.approval.pk, actor=self.user)
        services.approve(self.approval.pk, actor=self.user)
        self.messages.send.assert_called_once()
        self.assertEqual(result.data['status'], ApprovalStatus.SUCCEEDED)
        self.assertEqual(
            self.approval.executed_effects.get().effect_ref, 'gmail-recording-1'
        )
        _, message = self._sent_message()
        self.assertEqual(message['To'], 'lokesh@equa.work')
        self.assertEqual(message['Cc'], 'copy@equa.work')
        self.assertEqual(message['Bcc'], 'audit@equa.work')
        self.assertEqual(
            message['Message-ID'],
            commands.message_reference(self.approval.idempotency_key),
        )

    def test_email_permission_is_rechecked_not_inferred_from_review_role(self):
        """A reviewer without email send capability cannot reach the provider."""
        self.user.groups.clear()
        result = services.approve(self.approval.pk, actor=self.user)
        self.messages.send.assert_not_called()
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )

    def test_recipient_policy_blocks_bcc_before_provider(self):
        """Every recipient field is policy-checked on the real execution path."""
        self.approval.payload['bcc'] = ['hidden@external.test']
        self.approval.save(update_fields=['payload'])
        result = services.approve(self.approval.pk, actor=self.user)
        self.messages.send.assert_not_called()
        self.assertTrue(result.data['execution_result']['blocked_by_policy'])
        self.assertFalse(ExecutedEffect.objects.filter(approval=self.approval).exists())

    def test_provider_setup_failure_is_proven_not_sent(self):
        """Failure before execute starts is distinguished from a lost response."""
        self.client_stub._get_service.side_effect = RuntimeError('test setup failure')
        result = services.approve(self.approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )
        self.messages.send.assert_not_called()

    def test_lost_response_reconciles_exact_sent_message_without_resend(self):
        """RFC identifier, all headers and complete body must agree with Sent."""
        self.messages.send.return_value.execute.side_effect = TimeoutError
        result = services.approve(self.approval.pk, actor=self.user)
        self.assertEqual(result.data['execution_result']['execution_state'], 'unknown')
        raw, _ = self._sent_message()
        self.messages.list.return_value.execute.return_value = {
            'messages': [{'id': 'gmail-recording-1'}]
        }
        self.messages.get.return_value.execute.return_value = {
            'id': 'gmail-recording-1',
            'threadId': 'thread-recording-1',
            'raw': raw,
            'labelIds': ['SENT'],
        }
        result = reconcile_execution(self.approval.pk)
        self.messages.send.assert_called_once()
        self.messages.modify.assert_not_called()
        self.assertEqual(result.status, ApprovalStatus.SUCCEEDED)

    def test_unknown_with_no_exact_match_does_not_claim_failure_or_resend(self):
        """Provider absence is not proof of no dispatch."""
        self.messages.send.return_value.execute.side_effect = TimeoutError
        services.approve(self.approval.pk, actor=self.user)
        self.messages.list.return_value.execute.return_value = {'messages': []}
        result = reconcile_execution(self.approval.pk)
        self.assertEqual(result.execution_result['execution_state'], 'unknown')
        services.approve(self.approval.pk, actor=self.user)
        self.messages.send.assert_called_once()

    def test_spoofed_sent_message_with_matching_identifier_stays_unknown(self):
        """An RFC identifier by itself is not an authoritative content receipt."""
        self.messages.send.return_value.execute.side_effect = TimeoutError
        services.approve(self.approval.pk, actor=self.user)
        _, message = self._sent_message()
        message.replace_header('To', 'someone-else@equa.work')
        self.messages.list.return_value.execute.return_value = {
            'messages': [{'id': 'other'}]
        }
        self.messages.get.return_value.execute.return_value = {
            'id': 'other',
            'threadId': 'thread-other',
            'labelIds': ['SENT'],
            'raw': base64.urlsafe_b64encode(message.as_bytes()).decode(),
        }
        result = reconcile_execution(self.approval.pk)
        self.assertEqual(result.execution_result['execution_state'], 'unknown')
        self.messages.send.assert_called_once()

    def test_missing_durable_authority_never_reaches_provider(self):
        """An in-memory object cannot stand in for the persisted dispatch key."""
        result = EmailExecutor().execute_for_approval(
            self.approval,
            actor=self.user,
            execution=SimpleNamespace(
                pk='not-persisted', approval_id=self.approval.pk, actor_id=self.user.pk
            ),
        )
        self.assertFalse(result.success)
        self.assertEqual(result.outcome, 'failed_before_effect')
        self.messages.send.assert_not_called()

    def test_injected_headers_and_malformed_attachments_fail_before_provider(self):
        """Canonical validation rejects invalid types and MIME/header injection."""
        for patch_payload in (
            {'subject': 'Test\r\nBcc: elsewhere@external.test'},
            {'reply_to': 'one@equa.work, two@equa.work'},
            {'attachments': 42},
            {'attachments': [{'filename': 42, 'data_bytes': b'test'}]},
            {
                'attachments': [
                    {
                        'filename': 'test.txt',
                        'data_bytes': b'test',
                        'mime_type': 'text/plain\r\nBcc: bad@external.test',
                    }
                ]
            },
        ):
            payload = {**self.approval.payload, **patch_payload}
            self.assertTrue(commands.validate_message(payload))
            result = commands.send_message(
                payload, actor=self.user, operation_id='invalid-header'
            )
            self.assertEqual(result['outcome'], 'failed_before_effect')
        self.messages.send.assert_not_called()
