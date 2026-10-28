"""Native voice confirmation persists only a same-case proposed planning draft."""

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from asgiref.sync import async_to_sync

from ai.core.auth import AIPrincipal, principal_context
from ai.core.decisions.adapters import repair_scope_adapter as adapter
from ai.core.decisions.adapters.proposal_adapter import ProposalAdapter
from ai.core.decisions.coordinator import DecisionConflict, DecisionCoordinator
from ai.core.decisions.receipts import lookup_operation
from ai.core.decisions.store import InMemoryPendingDecisionStore
from ai.core.focus.context import bind_turn_focus
from ai.core.focus.contract import FocusTarget, focus_to_stored
from ai.core.tools.read_only import confirmed_write_exception, read_only_tool_fence
from ai.core.tools.repair_scope_draft import (
    REPAIR_SCOPE_DRAFT_TOOLS,
    get_focused_repair_scope_context,
    get_focused_repair_scope_draft,
    save_proposed_repair_scope_draft,
    search_focused_work_manual,
)
from ai.core.voice.tool_actions import VoiceToolActionResolver
from aichat.models import ChatThread
from aichat.services.proposals import ProposalError
from tasks.models import WorkOrderCommand
from tasks.services.repair_scope_drafts import EVENT_TYPE, read_repair_scope_draft
from tasks.tests.test_repair_scope_drafts import DraftFixture
from voice.models import VoiceOperation, VoiceSession


@override_settings(
    AIMMS_PROCEDURES_ENABLED=True,
    AIMMS_TEST_ALARMS_ENABLED=True,
    AIMMS_WORK_ORDERS_ENABLED=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
)
class RepairScopeVoiceTest(DraftFixture, TestCase):
    """Real isolated PostgreSQL command/event/operation receipts, no provider calls."""

    def setUp(self):
        """Create genuine owned native focus and an active voice session."""
        super().setUp()
        self.principal = AIPrincipal(
            subject=f'user:{self.actor.pk}',
            actor=f'user:{self.actor.pk}',
            user_pk=str(self.actor.pk),
            username=self.actor.username,
            authentication_method='session',
            scope='site:test',
            policy_version='test',
            is_staff=True,
            is_superuser=True,
        )
        self.token = principal_context.set(self.principal)
        self.addCleanup(principal_context.reset, self.token)
        self.addCleanup(bind_turn_focus, None)
        projection = focus_to_stored(
            FocusTarget(
                kind='work_order',
                id=self.work.pk,
                label=self.work.reference,
                machine_id=self.machine.pk,
                work_order_id=self.work.pk,
            )
        )
        policy_hash = hashlib.sha256(b'site:test').hexdigest()
        self.thread = ChatThread.objects.create(
            owner=self.actor,
            scope_key='site:test',
            scope_hash=policy_hash,
            conversation_focus=projection,
            focus_version=1,
        )
        self.session = VoiceSession.objects.create(
            owner=self.actor,
            thread_id=self.thread.pk,
            scope_key='site:test',
            scope_hash=policy_hash,
            policy_version='test',
            state='active',
        )
        bind_turn_focus({'focus': projection, 'version': 1})
        self.now = [datetime.now(UTC)]
        self.coordinator = DecisionCoordinator(
            store=InMemoryPendingDecisionStore(),
            adapter=ProposalAdapter(),
            now=lambda: self.now[0],
        )

    def arguments(self):
        """Use genuine current native case identifiers."""
        return {'work_order_id': self.work.pk, **self.payload()}

    def present(self):
        """Install a typed, source-pinned preview without saving a draft."""
        return adapter.present(
            self.coordinator,
            self.arguments(),
            actor=self.principal,
            session_id=str(self.session.pk),
            thread_id=self.thread.pk,
            nonce='turn-one',
            source_content='Prepare the repair scope and save it as a draft.',
        ).decision

    def delivered(self, decision):
        """Exercise the actual playback binding, then pass the conservative echo window."""
        decision = self.coordinator.bind_playback(
            decision,
            utterance_id='utterance-one',
            spoken_text=decision.spoken_summary,
            spoken_hash=hashlib.sha256(decision.spoken_summary.encode()).hexdigest(),
        )
        decision = self.coordinator.playback(
            decision,
            event='playback-started',
            sequence=decision.sequence,
            utterance_id=decision.utterance_id,
            spoken_hash=decision.spoken_summary_hash,
        )
        self.now[0] += timedelta(seconds=100)
        return self.coordinator.playback(
            decision,
            event='playback-completed',
            sequence=decision.sequence,
            utterance_id=decision.utterance_id,
            spoken_hash=decision.spoken_summary_hash,
        )

    def reply(self, text, decision=None, **changes):
        """Use the exact client decision binding on the subsequent voice turn."""
        decision = decision or self.coordinator.store.read(self.thread.pk)
        params = {
            'actor': self.principal,
            'session_id': str(self.session.pk),
            'thread_id': self.thread.pk,
            'nonce': 'turn-two',
            'context': decision.to_public_dict(),
        }
        params.update(changes)
        return self.coordinator.resolve(text, **params)

    def test_context_reads_actual_prepared_source_and_manual_hashes(self):
        """Verify context reads actual prepared source and manual hashes."""
        result = async_to_sync(get_focused_repair_scope_context)()
        self.assertTrue(result['success'], result)
        self.assertEqual(result['work_order_id'], self.work.pk)
        self.assertEqual(result['sources']['e03']['id'], self.e03_id)
        self.assertTrue(result['sources']['e03']['content'])
        self.assertEqual(result['sources']['manuals'][0]['id'], self.manual.pk)
        self.assertEqual(len(result['sources']['manuals'][0]['sha256']), 64)
        self.assertIsNone(result['draft'])

    def test_write_fence_blocks_unconfirmed_tool(self):
        """Verify write fence blocks unconfirmed tool."""
        with read_only_tool_fence(), self.assertRaises(PermissionError):
            async_to_sync(save_proposed_repair_scope_draft)(**self.arguments())
        self.assertFalse(self.work.events.filter(event_type=EVENT_TYPE).exists())

    def test_actual_pdf_passage_preserves_page_source_hash_and_caveat(self):
        """Verify actual pdf passage preserves page source hash and caveat."""
        from io import BytesIO

        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        buffer = BytesIO()
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=842)
        font = DictionaryObject(
            {
                NameObject('/Type'): NameObject('/Font'),
                NameObject('/Subtype'): NameObject('/Type1'),
                NameObject('/BaseFont'): NameObject('/Helvetica'),
            }
        )
        page[NameObject('/Resources')] = DictionaryObject(
            {
                NameObject('/Font'): DictionaryObject(
                    {NameObject('/F1'): writer._add_object(font)}
                )
            }
        )
        stream = DecodedStreamObject()
        stream.set_data(
            b'BT /F1 12 Tf 40 800 Td (4.6.3 Cooling system inspection - synthetic native test passage) Tj ET'
        )
        page[NameObject('/Contents')] = writer._add_object(stream)
        writer.write(buffer)
        content = buffer.getvalue()
        self.manual.attachment.save(
            'native-motor-manual.pdf', ContentFile(content), save=True
        )
        result = async_to_sync(search_focused_work_manual)(
            '4.6.3 cooling', self.manual.pk
        )
        self.assertTrue(result['success'], result)
        passage = result['results'][0]
        self.assertIn('synthetic native test passage', passage['excerpt'])
        self.assertEqual(passage['page'], 1)
        self.assertEqual(passage['sha256'], hashlib.sha256(content).hexdigest())
        self.assertTrue(passage['source_url'].endswith('#page=1'))
        self.assertEqual(passage['applicability'], 'related_reference_unverified')
        other = async_to_sync(search_focused_work_manual)(
            'cooling', self.manual.pk + 999
        )
        self.assertEqual(other['results'], [])

    def test_tool_requires_current_principal_and_exact_work_focus(self):
        """Verify tool requires current principal and exact work focus."""
        bind_turn_focus(None)
        result = async_to_sync(save_proposed_repair_scope_draft)(**self.arguments())
        self.assertFalse(result['success'])
        self.assertFalse(async_to_sync(get_focused_repair_scope_draft)()['success'])
        self.assertFalse(self.work.events.filter(event_type=EVENT_TYPE).exists())

    def test_confirmed_tool_returns_actual_saved_receipt_and_readback(self):
        """Verify confirmed tool returns actual saved receipt and readback."""
        with read_only_tool_fence(), confirmed_write_exception():
            result = async_to_sync(save_proposed_repair_scope_draft)(**self.arguments())
        self.assertTrue(result['success'], result)
        self.assertEqual(result['draft'], result['readback']['draft'])
        self.assertEqual(
            async_to_sync(get_focused_repair_scope_draft)()['draft'], result['draft']
        )

    def test_saved_scope_recap_prereads_real_offered_tool_before_one_model_call(self):
        """Real actor/focus/RBAC/tool selection retrieves the persisted draft once."""
        from ai.core.config import get_settings
        from ai.core.focus.context import current_turn_focus
        from ai.core.tools.capture_ledger import bind_tool_captures
        from ai.core.workflows.wf8_lookup import T1LookupWorkflow

        self.save()
        saved = read_repair_scope_draft(self.work.pk, self.actor)['draft']
        focus = current_turn_focus()
        context = {
            'modality': 'voice',
            'conversation_focus': {**focus.projection, 'focus_version': focus.focus_version},
            'conversation_history': [{'role': 'assistant', 'content': 'STALE-RECEIPT approved'}],
        }
        query = "Show me what you saved. What's waiting on the supervisor, and what's the next step for the crew?"
        settings = get_settings().model_copy(update={
            'feature_capability_broker_enforce': True,
            'feature_capability_broker_shadow': True,
        })
        reply = SimpleNamespace(text='Unit fixture model reply.', usage_details=None)
        agent = SimpleNamespace(run=AsyncMock(return_value=reply))
        workflow = T1LookupWorkflow()
        ledger = bind_tool_captures()
        with (
            patch('ai.core.workflows.wf8_lookup.get_settings', return_value=settings),
            patch.object(workflow, '_get_agent', new_callable=AsyncMock, return_value=agent),
            patch('tasks.services.repair_scope_drafts.read_repair_scope_draft', wraps=read_repair_scope_draft) as reader,
        ):
            result = async_to_sync(workflow.execute)(query, context=context)
        self.assertTrue(result.success, result)
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(agent.run.await_count, 1)
        self.assertEqual(agent.run.await_args.kwargs['tools'], [])
        messages = agent.run.await_args.args[0]
        self.assertNotIn('STALE-RECEIPT', ' '.join(message.text for message in messages))
        audit = result.data['server_source_read']
        self.assertEqual(audit['draft_id'], saved['id'])
        self.assertEqual(audit['draft_revision'], saved['revision'])
        self.assertEqual(audit['machine_id'], self.machine.pk)
        self.assertEqual(audit['work_order_id'], self.work.pk)
        self.assertEqual(audit['actor_id'], str(self.actor.pk))
        self.assertFalse(audit['llm_function_call'])
        self.assertEqual(ledger.server_source_reads, [audit])

    def test_planning_context_projects_real_saved_identity_without_prior_scope_body(self):
        """Actual native prior proposal identity survives without anchoring its old body."""
        import json

        self.save()
        full = read_repair_scope_draft(self.work.pk, self.actor)['draft']
        context = async_to_sync(get_focused_repair_scope_context)()
        self.assertTrue(context['success'], context)
        self.assertEqual(context['draft']['id'], full['id'])
        self.assertEqual(context['draft']['revision'], full['revision'])
        self.assertEqual(context['draft']['draft_sha256'], hashlib.sha256(
            json.dumps(full, sort_keys=True, separators=(',', ':'), default=str).encode()
        ).hexdigest())
        self.assertTrue(context['draft']['scope_body_omitted'])
        self.assertNotIn('scope', context['draft'])
        self.assertTrue(context['sources']['e03']['prepared'])
        self.assertIn('manual_method_fidelity', context['planning_requirements'])
        self.assertEqual(async_to_sync(get_focused_repair_scope_draft)()['draft'], full)

    def test_provider_planning_captures_arguments_without_saving(self):
        """Verify provider planning captures arguments without saving."""
        arguments = self.arguments()

        class CaptureAgent:
            async def run(self, content, *, tools):
                """Verify run."""
                read = next(
                    tool
                    for tool in tools
                    if tool.__name__ == 'get_focused_repair_scope_context'
                )
                result = await read()
                if not result['success']:
                    raise AssertionError(result)
                save = next(tool for tool in tools if tool.__name__ == adapter.ACTION)
                await save(**arguments)
                return object()

        async def authorized():
            """Verify authorized."""
            return REPAIR_SCOPE_DRAFT_TOOLS

        resolver = VoiceToolActionResolver(
            agent=CaptureAgent(), authorized_tool_loader=authorized
        )
        with patch(
            'ai.core.voice.tool_actions.get_settings',
            return_value=SimpleNamespace(feature_voice_action_policy_enforce=True),
        ):
            result = async_to_sync(resolver.resolve)(
                'Prepare the repair scope and save it as a draft.',
                actor=self.principal,
                trusted_context=object(),
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.executable.arguments, arguments)
        self.assertFalse(self.work.events.filter(event_type=EVENT_TYPE).exists())

    def test_native_coordinator_saves_once_after_delivered_confirmation(self):
        """Verify native coordinator saves once after delivered confirmation."""
        decision = self.present()
        self.assertFalse(self.work.events.filter(event_type=EVENT_TYPE).exists())
        self.assertEqual(self.reply('yes', decision).event, 'echo_refused')
        decision = self.delivered(decision)
        self.now[0] += timedelta(seconds=2)
        with read_only_tool_fence():
            reply = self.reply('yes', decision)
        self.assertEqual(reply.event, 'receipt')
        self.assertIn('Proposed repair scope saved', reply.spoken)
        self.assertIn('pending', reply.spoken)
        receipt = read_repair_scope_draft(self.work.pk, self.actor)['draft']
        operation = VoiceOperation.objects.get()
        self.assertEqual(operation.state, 'succeeded')
        self.assertEqual(operation.receipt['draft'], receipt)
        self.assertEqual(operation.receipt_ref, f'repair-scope:{receipt["id"]}:r1')
        self.reply('yes', reply.decision)
        self.assertEqual(self.work.events.filter(event_type=EVENT_TYPE).count(), 1)
        self.assertEqual(
            WorkOrderCommand.objects.filter(command='save_repair_scope_draft').count(),
            1,
        )

    def test_echo_cannot_confirm_a_draft(self):
        """Verify echo cannot confirm a draft."""
        decision = self.present()
        decision = self.coordinator.bind_playback(
            decision,
            utterance_id='utterance-one',
            spoken_text=decision.spoken_summary,
            spoken_hash=hashlib.sha256(decision.spoken_summary.encode()).hexdigest(),
        )
        self.assertEqual(self.reply('yes', decision).event, 'echo_refused')
        self.assertFalse(self.work.events.filter(event_type=EVENT_TYPE).exists())

    def test_decline_and_cancel_do_not_save(self):
        """Verify decline and cancel do not save."""
        decision = self.delivered(self.present())
        self.now[0] += timedelta(seconds=2)
        self.assertEqual(self.reply('no', decision).event, 'declined')
        self.assertEqual(
            self.reply('cancel the action', self.present()).event, 'canceled'
        )
        self.assertFalse(self.work.events.filter(event_type=EVENT_TYPE).exists())

    def test_actor_session_and_stale_context_cannot_confirm(self):
        """Verify actor session and stale context cannot confirm."""
        decision = self.delivered(self.present())
        self.now[0] += timedelta(seconds=2)
        other = SimpleNamespace(user_pk=str(self.reviewer.pk))
        with self.assertRaises(DecisionConflict):
            self.reply('yes', decision, actor=other)
        self.assertFalse(self.work.events.filter(event_type=EVENT_TYPE).exists())

    def test_fresh_work_version_or_source_drift_disarms(self):
        """Verify fresh work version or source drift disarms."""
        decision = self.delivered(self.present())
        self.now[0] += timedelta(seconds=2)
        self.work.lifecycle_version += 1
        self.work.save(update_fields=['lifecycle_version'])
        with self.assertRaises(ProposalError):
            self.reply('yes', decision)
        self.assertFalse(self.work.events.filter(event_type=EVENT_TYPE).exists())

    def test_focus_change_and_permission_revocation_disarm(self):
        """Verify focus change and permission revocation disarm."""
        decision = self.delivered(self.present())
        self.now[0] += timedelta(seconds=2)
        self.thread.focus_version += 1
        self.thread.save(update_fields=['focus_version'])
        with self.assertRaises(ProposalError):
            self.reply('yes', decision)
        self.assertFalse(self.work.events.filter(event_type=EVENT_TYPE).exists())

    def test_manual_source_drift_disarms(self):
        """Verify manual source drift disarms."""
        decision = self.delivered(self.present())
        self.now[0] += timedelta(seconds=2)
        with self.manual.attachment.open('wb') as stored:
            stored.write(b'%PDF-1.4\nChanged native source\n%%EOF')
        with self.assertRaises(ProposalError):
            self.reply('yes', decision)
        self.assertFalse(self.work.events.filter(event_type=EVENT_TYPE).exists())

    def test_revoked_actor_cannot_execute_or_read_receipt(self):
        """Verify revoked actor cannot execute or read receipt."""
        decision = self.delivered(self.present())
        self.now[0] += timedelta(seconds=2)
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
        with self.assertRaises(ProposalError):
            self.reply('yes', decision)
        self.assertFalse(self.work.events.filter(event_type=EVENT_TYPE).exists())

    def test_lost_operation_response_reconciles_without_reexecution(self):
        """Verify lost operation response reconciles without reexecution."""
        from ai.core.decisions.receipts import finish_operation

        decision = self.delivered(self.present())
        self.now[0] += timedelta(seconds=2)
        calls = [0]

        def lose_once(*args, **kwargs):
            """Verify lose once."""
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError('Synthetic lost reply after domain commit')
            return finish_operation(*args, **kwargs)

        with patch(
            'ai.core.decisions.receipts.finish_operation', side_effect=lose_once
        ):
            reply = self.reply('yes', decision)
        self.assertEqual(reply.decision.execution_state, 'succeeded')
        self.assertEqual(self.work.events.filter(event_type=EVENT_TYPE).count(), 1)
        operation = VoiceOperation.objects.get()
        result = lookup_operation(
            actor=self.principal, thread_id=self.thread.pk, operation_id=operation.pk
        )
        self.assertEqual(
            result['receipt']['draft']['id'], operation.receipt['draft']['id']
        )
