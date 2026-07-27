"""Feature #14 scoped chat: resolver, token, conversations, tools, citations.

Runs under the full InvenTree settings (PostgreSQL invoke runner); it is
skipped in the minimal aichat-only settings because it exercises the real
scope seam and work-order service readers.
"""

from __future__ import annotations

import unittest
import uuid
from unittest import mock

from django.apps import apps

if not apps.is_installed('tasks'):
    raise unittest.SkipTest('requires the full InvenTree app registry')

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from tasks.models import WorkOrder, WorkOrderLifecycle, WorkOrderType
from tasks.scope import MaintenanceScope
from tasks.workorder_models import WorkOrderEvent

from aichat.models import (
    ChatCitation,
    ChatThread,
    ChatToolInvocation,
    ConversationStatus,
    ScopedConversation,
    ThreadNamespace,
)
from aichat.services import ScopedThreadRejected, ThreadRepository
from aichat.services import context as context_service
from aichat.services import conversations as conversation_service
from aichat.services import tools as tool_service
from assets.models import AssetMachine
from company.models import Company

#: Mutable per-test scope table consulted by the deployment-seam resolver.
_SCOPES: dict[str, set[MaintenanceScope]] = {}


def _test_scope_resolver(actor):
    """Deployment-seam resolver reading the per-test scope table."""
    return _SCOPES.get(actor.get_username(), set())


SCOPED_FLAGS = {
    'AIMMS_SCOPED_CHAT_ENABLED': True,
    'AIMMS_SCOPED_CHAT_CONTEXTS': ['work_order'],
    'AIMMS_WORK_ORDERS_ENABLED': True,
    'AIMMS_MAINTENANCE_SCOPE_RESOLVER': f'{__name__}._test_scope_resolver',
}


@override_settings(**SCOPED_FLAGS)
class ScopedChatTestCase(TestCase):
    """Shared fixture: two customers, an in-scope actor, and an outsider."""

    @classmethod
    def setUpTestData(cls):
        """Create the scoped record graph once."""
        cls.customer = Company.objects.create(name='Scoped Cust', is_customer=True)
        cls.other_customer = Company.objects.create(
            name='Scoped Other Cust', is_customer=True
        )
        users = get_user_model().objects
        cls.actor = users.create_superuser(
            username='scoped-actor', email='sa@example.com', password='pw'
        )
        cls.outsider = users.create_superuser(
            username='scoped-outsider', email='so@example.com', password='pw'
        )
        cls.viewer = users.create_user(
            username='scoped-viewer', email='sv@example.com', password='pw'
        )
        cls.machine = AssetMachine.objects.create(
            name='Lathe 3', customer=cls.customer
        )

    def setUp(self):
        """Reset the scope table and create a fresh in-progress work order."""
        _SCOPES.clear()
        _SCOPES['scoped-actor'] = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        _SCOPES['scoped-viewer'] = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        _SCOPES['scoped-outsider'] = {
            MaintenanceScope(customer_id=self.other_customer.pk, site_key=None)
        }
        self.work_order = WorkOrder.objects.create(
            title='Inspect spindle',
            status=WorkOrder.STATUS_REVIEW,
            priority=WorkOrder.PRIORITY_MEDIUM,
            customer=self.customer,
            machine=self.machine,
            assigned_to=self.actor,
            work_order_type=WorkOrderType.PREVENTIVE,
            lifecycle_status=WorkOrderLifecycle.IN_PROGRESS,
            reference='WO-TEST-01',
        )

    def _resolve(self, user=None):
        """Resolve a work-order context for a user."""
        return context_service.resolve_context(
            user or self.actor,
            context_type='work_order',
            object_id=str(self.work_order.pk),
        )

    def _conversation(self, user=None):
        """Create one scoped conversation from a fresh resolution."""
        user = user or self.actor
        return conversation_service.create_conversation(
            owner=user, context=self._resolve(user)
        )


class ContextResolverTests(ScopedChatTestCase):
    """FR-SCH-001/003: scope-filtered, permission-checked, allow-listed."""

    def test_resolution_returns_signed_context_with_allowlisted_snapshot(self):
        """Resolution mints a token and only allow-listed snapshot fields."""
        context = self._resolve()
        self.assertEqual(context.context_type, 'work_order')
        self.assertEqual(context.object_id, str(self.work_order.pk))
        self.assertIn('WO-TEST-01', context.display_label)
        self.assertIn('qa', context.capabilities)
        self.assertTrue(context.token)
        self.assertTrue(context.source_revision.startswith('v1:'))
        self.assertEqual(
            set(context.snapshot),
            {
                'reference',
                'title',
                'lifecycle_status',
                'work_order_type',
                'priority',
                'lifecycle_version',
                'machine',
                'assigned_to',
                'due_date',
                'scheduled_start',
                'scheduled_end',
            },
        )
        self.assertNotIn('description', context.snapshot)

    def test_master_switch_and_context_list_fail_closed(self):
        """Disabled flags yield CONTEXT_TYPE_UNKNOWN before any lookup."""
        with self.settings(AIMMS_SCOPED_CHAT_ENABLED=False):
            with self.assertRaises(context_service.ContextTypeUnknown):
                self._resolve()
        with self.settings(AIMMS_SCOPED_CHAT_CONTEXTS=[]):
            with self.assertRaises(context_service.ContextTypeUnknown):
                self._resolve()
        with self.settings(AIMMS_WORK_ORDERS_ENABLED=False):
            with self.assertRaises(context_service.ContextTypeUnknown):
                self._resolve()

    def test_enabled_but_unregistered_type_stays_unknown(self):
        """Listing a type in settings cannot enable an unregistered resolver."""
        with self.settings(AIMMS_SCOPED_CHAT_CONTEXTS=['work_order', 'asset']):
            with self.assertRaises(context_service.ContextTypeUnknown):
                context_service.resolve_context(
                    self.actor, context_type='asset', object_id='1'
                )

    def test_out_of_scope_and_missing_records_are_indistinguishable(self):
        """CONTEXT_FORBIDDEN is scope-safe: same code for both denials."""
        with self.assertRaises(context_service.ContextForbidden) as denied:
            self._resolve(self.outsider)
        with self.assertRaises(context_service.ContextForbidden) as missing:
            context_service.resolve_context(
                self.actor, context_type='work_order', object_id='999999'
            )
        self.assertEqual(denied.exception.code, missing.exception.code)

    def test_unresolved_scope_fails_closed(self):
        """An actor without any scope cannot resolve anything."""
        _SCOPES.pop('scoped-actor')
        with self.assertRaises(context_service.ContextForbidden):
            self._resolve()

    def test_capabilities_follow_proposals_flag_and_permission(self):
        """Proposal capabilities appear only with the flag and the permission."""
        self.assertEqual(self._resolve().capabilities, ('qa',))
        with self.settings(AIMMS_SCOPED_CHAT_PROPOSALS=True):
            self.assertIn('propose_hold', self._resolve().capabilities)
            # A plain user without tasks.transition_workorder stays read-only.
            self.assertEqual(self._resolve(self.viewer).capabilities, ('qa',))


class ContextTokenTests(ScopedChatTestCase):
    """FR-SCH-001/004: token binding, expiry, and forgery rejection."""

    def test_token_round_trip_binds_user_object_and_revision(self):
        """A minted token validates only for its exact binding."""
        context = self._resolve()
        claims = context_service.validate_context_token(
            self.actor,
            context.token,
            expected_type='work_order',
            expected_object_id=context.object_id,
        )
        self.assertEqual(claims['sub'], str(self.actor.pk))
        self.assertEqual(claims['revision'], context.source_revision)

    def test_token_is_rejected_for_another_user(self):
        """A stolen token is inert for any other principal."""
        context = self._resolve()
        with self.assertRaises(context_service.ContextTokenInvalid):
            context_service.validate_context_token(self.outsider, context.token)

    def test_tampered_and_empty_tokens_are_rejected(self):
        """Signature validation rejects forgeries and empty values."""
        context = self._resolve()
        with self.assertRaises(context_service.ContextTokenInvalid):
            context_service.validate_context_token(
                self.actor, context.token[:-2] + 'xx'
            )
        with self.assertRaises(context_service.ContextTokenInvalid):
            context_service.validate_context_token(self.actor, '')

    def test_token_expiry_is_enforced(self):
        """An aged-out token raises CONTEXT_TOKEN_EXPIRED."""
        context = self._resolve()
        with mock.patch.object(context_service, 'token_ttl_seconds', return_value=0):
            with self.assertRaises(context_service.ContextTokenExpired):
                context_service.validate_context_token(self.actor, context.token)

    def test_token_binding_to_wrong_object_is_rejected(self):
        """A token for one record cannot address another."""
        context = self._resolve()
        with self.assertRaises(context_service.ContextTokenInvalid):
            context_service.validate_context_token(
                self.actor,
                context.token,
                expected_type='work_order',
                expected_object_id='424242',
            )

    def test_credential_rotation_invalidates_the_token(self):
        """The session-auth-hash binding dies with a password change."""
        context = self._resolve()
        self.actor.set_password('new-password')
        self.actor.save()
        with self.assertRaises(context_service.ContextTokenInvalid):
            context_service.validate_context_token(self.actor, context.token)


class ConversationBoundaryTests(ScopedChatTestCase):
    """FR-SCH-010/011: owner- and scope-bound governance rows."""

    def test_create_binds_a_scoped_namespace_transcript(self):
        """Creation allocates a scoped thread legacy repositories reject."""
        conversation = self._conversation()
        self.assertTrue(conversation.ai_thread_id.startswith('scoped_'))
        thread = ChatThread.objects.get(pk=conversation.ai_thread_id)
        self.assertEqual(thread.namespace, ThreadNamespace.SCOPED)
        legacy = ThreadRepository(
            self.actor, conversation.scope_key, namespace=ThreadNamespace.UNSCOPED
        )
        with self.assertRaises(ScopedThreadRejected):
            legacy.get(conversation.ai_thread_id)

    def test_cross_owner_lookup_is_indistinguishable_from_missing(self):
        """Another actor cannot see, rename, or delete the conversation."""
        conversation = self._conversation()
        _, outsider_hash = context_service.actor_scope_strings(self.outsider)
        with self.assertRaises(conversation_service.ConversationNotFound):
            conversation_service.get_conversation(
                owner=self.outsider,
                scope_hash=outsider_hash,
                conversation_id=conversation.pk,
            )
        self.assertEqual(
            conversation_service.list_conversations(
                owner=self.outsider, scope_hash=outsider_hash
            ),
            [],
        )

    def test_delete_tombstones_governance_and_removes_transcript(self):
        """Deletion removes transcript rows but keeps the audit tombstone."""
        conversation = self._conversation()
        _, scope_hash = context_service.actor_scope_strings(self.actor)
        conversation_service.delete_conversation(
            owner=self.actor, scope_hash=scope_hash, conversation_id=conversation.pk
        )
        conversation.refresh_from_db()
        self.assertEqual(conversation.status, ConversationStatus.DELETED)
        self.assertIsNotNone(conversation.deleted_at)
        self.assertFalse(
            ChatThread.objects.filter(pk=conversation.ai_thread_id).exists()
        )
        self.assertEqual(
            conversation_service.list_conversations(
                owner=self.actor, scope_hash=scope_hash
            ),
            [],
        )

    def test_closed_conversation_rejects_rename(self):
        """A closed conversation is read only."""
        conversation = self._conversation()
        _, scope_hash = context_service.actor_scope_strings(self.actor)
        conversation_service.close_conversation(
            owner=self.actor, scope_hash=scope_hash, conversation_id=conversation.pk
        )
        with self.assertRaises(conversation_service.ConversationReadOnly):
            conversation_service.rename_conversation(
                owner=self.actor,
                scope_hash=scope_hash,
                conversation_id=conversation.pk,
                title='New title',
            )


class ScopedToolTests(ScopedChatTestCase):
    """FR-SCH-004/005: per-call authorization, typed args, audit rows."""

    @staticmethod
    def _utc_dt(hour):
        """Build a schedule datetime respecting USE_TZ (False under test on sqlite)."""
        from datetime import datetime, timezone as dt_timezone

        from django.conf import settings

        value = datetime(2026, 8, 3, hour, tzinfo=dt_timezone.utc)
        return value if settings.USE_TZ else value.replace(tzinfo=None)

    def _invoke(self, tool, arguments=None, *, user=None, turn='turn-1'):
        """Invoke one tool for the fixture conversation."""
        if not hasattr(self, 'conversation'):
            self.conversation = self._conversation()
        return tool_service.invoke_tool(
            user=user or self.actor,
            conversation=self.conversation,
            tool_name=tool,
            arguments=arguments,
            turn_key=turn,
        )

    def test_registry_lists_exactly_the_work_order_tools(self):
        """The read-only registry is the complete tool surface."""
        self.assertEqual(
            tool_service.tools_for_context('work_order'),
            (
                'schedule_conflicts',
                'schedule_preview',
                'work_order_events_page',
                'work_order_kit_status',
                'work_order_readiness',
                'work_order_schedule',
                'work_order_steps',
                'work_order_summary',
            ),
        )
        self.assertEqual(tool_service.tools_for_context('asset'), ())

    def test_schedule_tool_returns_the_pinned_window_and_version(self):
        """The schedule read tool exposes the card's own schedule, scoped."""
        self.work_order.scheduled_start = self._utc_dt(9)
        self.work_order.scheduled_end = self._utc_dt(13)
        self.work_order.estimated_minutes = 240
        self.work_order.save()

        envelope = self._invoke('work_order_schedule')
        self.assertTrue(envelope['authorized'])
        result = envelope['result']
        self.assertEqual(result['work_order_id'], self.work_order.pk)
        self.assertEqual(result['estimated_minutes'], 240)
        self.assertEqual(result['machine_name'], 'Lathe 3')
        self.assertEqual(
            result['lifecycle_version'], self.work_order.lifecycle_version
        )
        self.assertIsNotNone(result['scheduled_start'])

    def test_conflicts_tool_flags_a_same_machine_overlap(self):
        """The conflicts tool reports overlaps that involve the pinned card."""
        _utc = self._utc_dt

        self.work_order.scheduled_start = _utc(9)
        self.work_order.scheduled_end = _utc(12)
        self.work_order.save()
        # Another card on the same machine overlapping the pinned window.
        WorkOrder.objects.create(
            title='Overlapper', status='backlog', priority='low',
            customer=self.customer, machine=self.machine,
            scheduled_start=_utc(11), scheduled_end=_utc(14),
        )

        envelope = self._invoke('schedule_conflicts')
        self.assertTrue(envelope['authorized'])
        conflicts = envelope['result']['conflicts']
        self.assertEqual(len(conflicts), 1)
        self.assertIn(self.work_order.pk, conflicts[0]['card_ids'])

    def test_conflicts_tool_is_empty_for_an_unscheduled_card(self):
        """An unscheduled card cannot clash; the tool says so rather than erroring."""
        envelope = self._invoke('schedule_conflicts')
        self.assertTrue(envelope['authorized'])
        self.assertEqual(envelope['result']['conflicts'], [])

    @override_settings(USE_TZ=True, **SCOPED_FLAGS)
    def test_preview_tool_returns_planner_operations_without_writing(self):
        """The preview tool runs the planner read-only; nothing is persisted."""
        self.work_order.estimated_minutes = 120
        self.work_order.scheduled_start = None
        self.work_order.scheduled_end = None
        self.work_order.save()

        envelope = self._invoke('schedule_preview')
        self.assertTrue(envelope['authorized'])
        result = envelope['result']
        self.assertTrue(
            any(op['card_id'] == self.work_order.pk for op in result['operations'])
        )
        # No write: the card is still unscheduled after a preview.
        self.work_order.refresh_from_db()
        self.assertIsNone(self.work_order.scheduled_start)

    def test_summary_tool_returns_snapshot_with_citation_and_audit(self):
        """A successful call stamps a citation and an allowed audit row."""
        envelope = self._invoke('work_order_summary')
        self.assertTrue(envelope['authorized'])
        self.assertEqual(envelope['result']['summary']['reference'], 'WO-TEST-01')
        citation = ChatCitation.objects.get(pk=envelope['citation_id'])
        self.assertEqual(citation.source_type, 'tool_result')
        self.assertEqual(citation.source_revision, envelope['source_revision'])
        invocation = ChatToolInvocation.objects.get(pk=envelope['invocation_id'])
        self.assertEqual(invocation.authorization_result, 'allowed')
        self.assertEqual(invocation.tool, 'work_order_summary')

    def test_readiness_tool_reports_the_live_evaluator_envelope(self):
        """The readiness tool passes the evaluator through unchanged."""
        envelope = self._invoke('work_order_readiness', {'action': 'hold'})
        self.assertTrue(envelope['authorized'])
        result = envelope['result']
        self.assertEqual(result['action'], 'hold')
        self.assertTrue(result['ready'])
        self.assertIn('snapshot_hash', result)
        blocked = self._invoke(
            'work_order_readiness', {'action': 'start'}, turn='turn-2'
        )
        codes = {item['code'] for item in blocked['result']['blockers']}
        # in_progress cannot 'start' again: only the real evaluator's codes.
        self.assertIn('READINESS_ERROR', codes)

    def test_steps_kit_and_events_tools_return_bounded_pages(self):
        """Reader tools return bounded, truncation-flagged results."""
        steps = self._invoke('work_order_steps', {'limit': 5})
        self.assertTrue(steps['authorized'])
        self.assertIsNone(steps['result']['application'])
        kit = self._invoke('work_order_kit_status', turn='turn-2')
        self.assertIsNone(kit['result']['kit'])
        for index in range(3):
            WorkOrderEvent.objects.create(
                work_order=self.work_order,
                event_type='hold',
                from_status='in_progress',
                to_status='on_hold',
                actor=self.actor,
                correlation_id=uuid.uuid4(),
                reason=f'event {index}',
            )
        events = self._invoke(
            'work_order_events_page', {'limit': 2, 'offset': 0}, turn='turn-3'
        )
        self.assertEqual(len(events['result']['events']), 2)
        self.assertEqual(events['result']['total'], 3)
        self.assertTrue(events['result']['truncated'])

    def test_unknown_tool_and_invalid_arguments_are_rejected(self):
        """The typed schema is the only accepted argument surface."""
        with self.assertRaises(tool_service.ToolNotAvailable):
            self._invoke('work_order_delete')
        with self.assertRaises(tool_service.ToolArgumentsInvalid):
            self._invoke('work_order_summary', {'unexpected': 1})
        with self.assertRaises(tool_service.ToolArgumentsInvalid):
            self._invoke('work_order_readiness', {'action': 'DROP TABLE'})
        with self.assertRaises(tool_service.ToolArgumentsInvalid):
            self._invoke('work_order_events_page', {'limit': 5000})
        with self.assertRaises(tool_service.ToolArgumentsInvalid):
            self._invoke('work_order_steps', {'limit': '10'})

    def test_mid_conversation_revocation_denies_the_next_call(self):
        """SC-SCH-006: revocation takes effect on the very next tool call."""
        first = self._invoke('work_order_summary')
        self.assertTrue(first['authorized'])
        _SCOPES.pop('scoped-actor')
        denied = self._invoke('work_order_summary', turn='turn-2')
        self.assertFalse(denied['authorized'])
        self.assertEqual(denied['error'], 'not authorized')
        self.assertIsNone(denied['result'])
        row = ChatToolInvocation.objects.filter(
            conversation=self.conversation, turn_key='turn-2'
        ).get()
        self.assertEqual(row.authorization_result, 'denied')

    def test_per_turn_tool_budget_is_enforced(self):
        """NFR-SCH-002: bounded fan-out per turn."""
        with self.settings(AIMMS_SCOPED_CHAT_MAX_TOOL_CALLS=2):
            self._invoke('work_order_summary')
            self._invoke('work_order_summary')
            with self.assertRaises(tool_service.ToolBudgetExceeded):
                self._invoke('work_order_summary')
        # A new turn gets a fresh budget.
        fresh = self._invoke('work_order_summary', turn='turn-2')
        self.assertTrue(fresh['authorized'])

    def test_closed_conversation_rejects_tool_calls(self):
        """CONVERSATION_READ_ONLY blocks every tool."""
        conversation = self._conversation()
        _, scope_hash = context_service.actor_scope_strings(self.actor)
        conversation_service.close_conversation(
            owner=self.actor, scope_hash=scope_hash, conversation_id=conversation.pk
        )
        conversation.refresh_from_db()
        with self.assertRaises(tool_service.ConversationReadOnly):
            tool_service.invoke_tool(
                user=self.actor,
                conversation=conversation,
                tool_name='work_order_summary',
                arguments=None,
                turn_key='turn-1',
            )


class ScopedChatApiTests(ScopedChatTestCase):
    """Session-authenticated REST rail over the same services."""

    def _client(self, user=None):
        """Return a logged-in test client."""
        self.client.force_login(user or self.actor)
        return self.client

    def _resolve_http(self, client):
        """Resolve the fixture work order over HTTP."""
        return client.post(
            '/api/aichat/context/resolve/',
            {'context_type': 'work_order', 'object_id': str(self.work_order.pk)},
            content_type='application/json',
        )

    def test_unauthenticated_requests_are_rejected(self):
        """Every scoped route requires the authenticated boundary."""
        paths = [
            ('/api/aichat/context/resolve/', 'post'),
            ('/api/aichat/conversations/', 'get'),
        ]
        for path, method in paths:
            response = getattr(self.client, method)(path)
            self.assertIn(response.status_code, (401, 403), path)

    def test_resolve_create_invoke_and_trace_round_trip(self):
        """The full pinned-chat flow works end to end over HTTP."""
        client = self._client()
        resolved = self._resolve_http(client)
        self.assertEqual(resolved.status_code, 200, resolved.content)
        body = resolved.json()
        self.assertIn('work_order_readiness', body['tools'])
        self.assertEqual(body['capabilities'], ['qa'])

        created = client.post(
            '/api/aichat/conversations/',
            {'token': body['token']},
            content_type='application/json',
        )
        self.assertEqual(created.status_code, 201, created.content)
        conversation = created.json()
        self.assertTrue(conversation['ai_thread_id'].startswith('scoped_'))
        self.assertEqual(conversation['context_state'], 'authorized')

        invoked = client.post(
            f'/api/aichat/conversations/{conversation["id"]}/tools/invoke/',
            {
                'token': body['token'],
                'tool': 'work_order_readiness',
                'arguments': {'action': 'hold'},
                'turn_key': 'turn-1',
            },
            content_type='application/json',
        )
        self.assertEqual(invoked.status_code, 200, invoked.content)
        envelope = invoked.json()
        self.assertTrue(envelope['authorized'])
        self.assertTrue(envelope['result']['ready'])

        citations = client.get(
            f'/api/aichat/conversations/{conversation["id"]}/citations/?turn=turn-1'
        )
        self.assertEqual(citations.status_code, 200)
        rows = citations.json()['results']
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['available'])
        self.assertEqual(rows[0]['locator']['tool'], 'work_order_readiness')

        trace = client.get(
            f'/api/aichat/conversations/{conversation["id"]}/tools/?turn=turn-1'
        )
        self.assertEqual(trace.status_code, 200)
        trace_rows = trace.json()['results']
        self.assertEqual(len(trace_rows), 1)
        self.assertEqual(trace_rows[0]['authorization_result'], 'allowed')

    def test_resolve_is_scope_safe_over_http(self):
        """Out-of-scope resolution has the missing-record shape."""
        client = self._client(self.outsider)
        denied = self._resolve_http(client)
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied.json()['error'], 'CONTEXT_FORBIDDEN')
        missing = client.post(
            '/api/aichat/context/resolve/',
            {'context_type': 'work_order', 'object_id': '999999'},
            content_type='application/json',
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()['error'], 'CONTEXT_FORBIDDEN')

    def test_disabled_feature_is_a_uniform_404(self):
        """The disabled surface does not reveal record existence."""
        client = self._client()
        with self.settings(AIMMS_SCOPED_CHAT_ENABLED=False):
            response = self._resolve_http(client)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error'], 'CONTEXT_TYPE_UNKNOWN')

    def test_conversation_detail_is_owner_bound_over_http(self):
        """Cross-owner conversation access is a plain 404."""
        conversation = self._conversation()
        client = self._client(self.outsider)
        response = client.get(f'/api/aichat/conversations/{conversation.pk}/')
        self.assertEqual(response.status_code, 404)

    def test_invoke_rejects_a_token_for_a_different_record(self):
        """The token must bind to the conversation's exact record."""
        conversation = self._conversation()
        other_order = WorkOrder.objects.create(
            title='Different order',
            status=WorkOrder.STATUS_REVIEW,
            priority=WorkOrder.PRIORITY_MEDIUM,
            customer=self.customer,
            machine=self.machine,
            lifecycle_status=WorkOrderLifecycle.IN_PROGRESS,
        )
        client = self._client()
        other_context = context_service.resolve_context(
            self.actor, context_type='work_order', object_id=str(other_order.pk)
        )
        response = client.post(
            f'/api/aichat/conversations/{conversation.pk}/tools/invoke/',
            {
                'token': other_context.token,
                'tool': 'work_order_summary',
                'turn_key': 'turn-1',
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'CONTEXT_TOKEN_INVALID')

    def test_revoked_access_renders_citations_unavailable(self):
        """SC-ADR-010: citations degrade to unavailable, never leak."""
        conversation = self._conversation()
        context = self._resolve()
        client = self._client()
        invoked = client.post(
            f'/api/aichat/conversations/{conversation.pk}/tools/invoke/',
            {
                'token': context.token,
                'tool': 'work_order_summary',
                'turn_key': 'turn-1',
            },
            content_type='application/json',
        )
        self.assertEqual(invoked.status_code, 200)

        # The record moves out of the actor's authority (customer change).
        self.work_order.customer = self.other_customer
        self.work_order.machine = None
        self.work_order.save(update_fields=['customer', 'machine', 'updated_at'])

        citations = client.get(
            f'/api/aichat/conversations/{conversation.pk}/citations/'
        )
        self.assertEqual(citations.status_code, 200)
        rows = citations.json()['results']
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['available'])
        self.assertNotIn('locator', rows[0])
        self.assertNotIn('source_id', rows[0])

        detail = client.get(f'/api/aichat/conversations/{conversation.pk}/')
        self.assertEqual(detail.json()['context_state'], 'revoked')

    def test_rename_close_and_delete_over_http(self):
        """Lifecycle operations stay owner-bound and tombstone-safe."""
        conversation = self._conversation()
        client = self._client()
        renamed = client.patch(
            f'/api/aichat/conversations/{conversation.pk}/',
            {'title': 'Spindle triage'},
            content_type='application/json',
        )
        self.assertEqual(renamed.status_code, 200, renamed.content)
        self.assertEqual(renamed.json()['title'], 'Spindle triage')

        closed = client.patch(
            f'/api/aichat/conversations/{conversation.pk}/',
            {'status': 'closed'},
            content_type='application/json',
        )
        self.assertEqual(closed.status_code, 200)
        self.assertEqual(closed.json()['status'], 'closed')

        deleted = client.delete(f'/api/aichat/conversations/{conversation.pk}/')
        self.assertEqual(deleted.status_code, 204)
        listing = client.get(
            f'/api/aichat/conversations/?context_type=work_order'
            f'&object_id={self.work_order.pk}'
        )
        self.assertEqual(listing.json()['results'], [])

    @override_settings(AIMMS_SCOPED_CHAT_PROPOSALS=True)
    def test_proposal_capability_flows_into_the_existing_rail(self):
        """A resolved propose capability drafts through the WS7 rail."""
        client = self._client()
        resolved = self._resolve_http(client).json()
        self.assertIn('propose_hold', resolved['capabilities'])

        conversation_response = client.post(
            '/api/aichat/conversations/',
            {'token': resolved['token']},
            content_type='application/json',
        )
        conversation = conversation_response.json()
        created = client.post(
            '/api/aichat/proposals/',
            {
                'action_type': 'work_order.hold',
                'work_order_id': self.work_order.pk,
                'reason': 'Scoped chat drafted hold',
                'thread_id': conversation['ai_thread_id'],
                'source_turn_id': 'turn-1',
            },
            content_type='application/json',
        )
        self.assertEqual(created.status_code, 201, created.content)
        proposal = created.json()
        confirmed = client.post(f'/api/aichat/proposals/{proposal["id"]}/confirm/')
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(confirmed.json()['receipt']['command'], 'hold')
        self.work_order.refresh_from_db()
        self.assertEqual(
            self.work_order.lifecycle_status, WorkOrderLifecycle.ON_HOLD
        )
