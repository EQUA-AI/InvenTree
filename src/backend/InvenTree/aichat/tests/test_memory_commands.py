"""Memory proposal rail qualification cases; no provider calls."""

import os
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from pydantic import SecretStr

from aichat.models import ChatMessage, ChatThread, MemoryFact, MemoryFactEvent
from aichat.services import memory_commands as commands
from aichat.services import memory_lifecycle, proposals
from aichat.services.memory_eligibility import MemoryEligibility


class MemoryCommandTests(TestCase):
    """Use the real shared proposal rail and portable fact schema."""

    def setUp(self):
        """Only external policy context is stubbed; lifecycle/receipts are real."""
        self.owner = get_user_model().objects.create_superuser(
            username='memory-command-owner',
            email='fixture@example.test',
            password='fixture-unused',
        )
        self.thread = ChatThread.objects.create(
            owner=self.owner,
            scope_key='site:main',
            scope_hash='fixture',
            memory_mode='extract',
        )
        self.source = ChatMessage.objects.create(
            thread=self.thread,
            sequence=1,
            role='user',
            content='Please remember my preference for a maintenance checklist.',
        )
        self.config = SimpleNamespace(
            feature_semantic_memory_extract_shadow=True,
            aimms_memory_fingerprint_key=SecretStr('fixture-key-' * 4),
        )
        self.enterContext(
            mock.patch.object(commands, 'get_settings', return_value=self.config)
        )
        self.enterContext(
            mock.patch.object(
                memory_lifecycle, 'get_settings', return_value=self.config
            )
        )
        self.enterContext(
            mock.patch.object(
                commands,
                'evaluate_memory_eligibility',
                return_value=MemoryEligibility(
                    'eligible', frozenset({'fixture'}), 'memory-v1'
                ),
            )
        )
        self.enterContext(mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '0'}))
        self.scope_key, self.scope_hash = commands.owner_scope(self.owner)
        self.fact = MemoryFact.objects.create(
            owner=self.owner,
            entity_kind='user',
            entity_id=str(self.owner.pk),
            slot_key='checklist',
            memory_type='user_preference',
            text='I prefer maintenance instructions to include a checklist.',
            text_lang='en',
            origin='compaction',
            origin_modality='chat',
            visibility_scope='user_private',
            classification='preference',
            source_thread=self.thread,
            source_message=self.source,
            notice_version='memory-v1',
            shield_state='clear',
            claim_fingerprint='a' * 64,
        )

    def propose(self, action='memory.remember', **overrides):
        """Prepare a single exact owner-bound target."""
        values = {
            'owner': self.owner,
            'scope_key': self.scope_key,
            'scope_hash': self.scope_hash,
            'action_type': action,
            'work_order_id': None,
            'reason': '',
            'idempotency_key': action,
            'policy_version': 'fixture',
            'intent': {
                'memory_fact_id': str(self.fact.pk),
                'expected_version': self.fact.version,
            },
        }
        values.update(overrides)
        return proposals.create_proposal(**values)

    def confirm(self, proposal, **kwargs):
        """The browser submits the hash it actually displayed."""
        return proposals.confirm_proposal(
            owner=self.owner,
            scope_hash=self.scope_hash,
            proposal_id=proposal.pk,
            expected_preview_hash=proposal.preview_hash,
            **kwargs,
        )

    def test_confirmation_and_replay_have_one_effect(self):
        """A preference is activated once and has a real confirming proposal."""
        proposal = self.propose()
        first = self.confirm(proposal)
        replay = self.confirm(proposal)
        self.assertEqual(first.receipt, replay.receipt)
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.lifecycle_state, 'active')
        self.assertEqual(self.fact.verification_class, 'user_confirmed')
        self.assertEqual(self.fact.confirming_proposal_id, proposal.pk)
        self.assertEqual(MemoryFactEvent.objects.filter(action='confirm').count(), 1)

    def test_missing_or_stale_preview_cannot_execute(self):
        """Unlike legacy actions, memory confirmation always requires its hash."""
        proposal = self.propose()
        with self.assertRaises(proposals.ProposalPreviewChanged):
            proposals.confirm_proposal(
                owner=self.owner, scope_hash=self.scope_hash, proposal_id=proposal.pk
            )
        MemoryFact.objects.filter(pk=self.fact.pk).update(version=2)
        with self.assertRaises(proposals.ProposalPreviewChanged):
            self.confirm(proposal)

    def test_unavailable_shield_and_inferred_operations_are_not_confirmable(self):
        """Neither a missing annotation nor an unverified operational claim passes."""
        self.fact.shield_state = 'unavailable'
        self.fact.save(update_fields=['shield_state'])
        with self.assertRaises(proposals.ProposalStateConflict):
            self.propose()
        self.fact.shield_state = 'clear'
        self.fact.memory_type = 'equipment_fact'
        self.fact.visibility_scope = 'client_shared'
        self.fact.classification = 'operational'
        self.fact.client_code = 'fixture'
        self.fact.entity_kind = 'machine'
        self.fact.entity_id = '1'
        self.fact.save()
        with (
            mock.patch.object(commands, '_can_read', return_value=True),
            self.assertRaises(proposals.ProposalRevalidationFailed),
        ):
            self.propose()

    def test_rejection_scrubs_copied_preview_and_withdraws_suggestion(self):
        """A returned decision payload cannot retain the removed fact's text."""
        proposal = self.propose()
        result = proposals.reject_proposal(
            owner=self.owner, scope_hash=self.scope_hash, proposal_id=proposal.pk
        )
        self.assertEqual(result.preview, {})
        self.assertEqual(result.intent, {})
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.lifecycle_state, 'withdrawn')
        self.assertEqual(self.fact.text, '')

    def test_decision_cap_does_not_block_forgetting(self):
        """Privacy deletion remains available after the suggestion-review allowance."""
        MemoryFactEvent.objects.bulk_create([
            MemoryFactEvent(owner_id=self.owner.pk, action='reject') for _ in range(20)
        ])
        proposal = self.propose()
        with self.assertRaises(proposals.ProposalStateConflict):
            self.confirm(proposal)
        forgotten = self.confirm(self.propose('memory.forget'))
        self.assertEqual(forgotten.receipt['status'], 'forgotten')

    def test_forget_all_requires_phrase_even_when_voice_claims_it_was_satisfied(self):
        """There is no legacy voice bypass for this destructive action."""
        from django.utils import timezone

        proposal = self.propose(
            'memory.forget_all', intent={'before': timezone.now().isoformat()}
        )
        with self.assertRaises(proposals.StrictConfirmationRequired):
            self.confirm(proposal, strict_phrase_satisfied=True)
        result = self.confirm(proposal, confirm_phrase='forget all my memories')
        self.assertEqual(result.receipt['status'], 'purged')

    def test_revoked_scope_hides_an_executed_preview(self):
        """Receipt replay must not become a route around current read scope."""
        proposal = self.propose()
        self.confirm(proposal)
        with (
            mock.patch.object(commands, '_can_read', return_value=False),
            self.assertRaises(proposals.ProposalNotFound),
        ):
            self.confirm(proposal)

    def test_retention_preserves_only_active_authority_receipt(self):
        """TTL cannot null an active preference's required confirmation identity."""
        from datetime import timedelta

        from django.utils import timezone

        from aichat.models import ChatActionProposal
        from aichat.services.retention import purge_terminal_proposals

        proposal = self.propose()
        self.confirm(proposal)
        ChatActionProposal.objects.filter(pk=proposal.pk).update(
            updated_at=timezone.now() - timedelta(days=401)
        )
        purge_terminal_proposals()
        proposal.refresh_from_db()
        self.assertEqual(proposal.preview, {})
        self.assertIsNotNone(proposal.receipt)
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.confirming_proposal_id, proposal.pk)

    def test_shared_preparation_is_idempotent_without_confirming_a_fact(self):
        """Browser/model preparation yields the same preview and leaves suggestion state."""
        intent = {
            'memory_fact_id': str(self.fact.pk),
            'expected_version': self.fact.version,
        }
        first = commands.prepare_action(
            self.owner,
            action_type='memory.remember',
            idempotency_key='shared-fixture',
            intent=intent,
        )
        second = commands.prepare_action(
            self.owner,
            action_type='memory.remember',
            idempotency_key='shared-fixture',
            intent=intent,
        )
        self.assertEqual(first.pk, second.pk)
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.lifecycle_state, 'proposed')

    def test_model_tool_returns_only_private_review_pointer_and_no_fact_content(self):
        """A model cannot turn proposal creation into confirmation or text recall."""
        from asgiref.sync import async_to_sync

        from ai.core.integrations.memory_tools import propose_memory_action

        with mock.patch(
            'ai.core.auth.get_current_principal',
            return_value=SimpleNamespace(user_pk=self.owner.pk),
        ):
            result = async_to_sync(propose_memory_action)(
                action='remember',
                request_key='tool-fixture',
                memory_fact_id=str(self.fact.pk),
                expected_version=self.fact.version,
            )
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertFalse(result['executed'])
        self.assertNotIn(self.fact.text, str(result))
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.lifecycle_state, 'proposed')

    def test_private_review_link_reauthorizes_owner(self):
        """A proposal identifier cannot disclose its preview to another actor."""
        from rest_framework.test import APIClient

        proposal = self.propose()
        client = APIClient()
        client.force_authenticate(self.owner)
        path = f'/api/aichat/memory/proposals/{proposal.pk}/decision/'
        self.assertEqual(client.get(path).status_code, 200)
        other = get_user_model().objects.create_superuser(
            username='memory-link-other',
            email='other@example.test',
            password='fixture-unused',
        )
        client.force_authenticate(other)
        response = client.get(path)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(self.fact.text, str(response.data))
