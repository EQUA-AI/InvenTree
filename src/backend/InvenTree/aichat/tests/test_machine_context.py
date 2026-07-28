"""Scoped chat pinned to a machine: registration, tools, and totality.

Runs under the full InvenTree settings (the invoke runner); it is skipped in
the minimal aichat-only settings because it exercises the real scope seam and
the asset/health model graph.
"""

from __future__ import annotations

import unittest
import unittest.mock
import uuid

from django.apps import apps

if not apps.is_installed('tasks'):
    raise unittest.SkipTest('requires the full InvenTree app registry')

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from tasks.scope import MaintenanceScope

from aichat.models import ChatCitation, ChatToolInvocation, ToolAuthorizationResult
from aichat.services import context as context_service
from aichat.services import conversations as conversation_service
from aichat.services import tools as tool_service
from assets.models import AssetMachine, Client
from company.models import Company

_SCOPES: dict[str, set[MaintenanceScope]] = {}


def _test_scope_resolver(actor):
    """Deployment-seam resolver reading the per-test scope table."""
    return _SCOPES.get(actor.get_username(), set())


MACHINE_FLAGS = {
    'AIMMS_SCOPED_CHAT_ENABLED': True,
    'AIMMS_SCOPED_CHAT_CONTEXTS': ['work_order', 'machine'],
    'AIMMS_ASSETS_ENABLED': True,
    'AIMMS_MACHINE_AI_READ_ENABLED': True,
    'AIMMS_MAINTENANCE_SCOPE_RESOLVER': f'{__name__}._test_scope_resolver',
}


@override_settings(**MACHINE_FLAGS)
class MachineContextTests(TestCase):
    """The machine context type resolves, pins and answers."""

    @classmethod
    def setUpTestData(cls):
        """Create two tenants and one asset each."""
        cls.customer = Company.objects.create(name='MC Cust', is_customer=True)
        cls.other_customer = Company.objects.create(name='MC Other', is_customer=True)
        cls.client_tenant = Client.objects.create(name='MC Plant', code='mc-plant')
        users = get_user_model().objects
        cls.actor = users.create_superuser(
            username='mc-actor', email='mc@example.com', password='pw'
        )
        cls.outsider = users.create_superuser(
            username='mc-outsider', email='mo@example.com', password='pw'
        )
        cls.machine = AssetMachine.objects.create(
            name='Mixer 2', customer=cls.customer, serial='MX-2'
        )
        cls.other_machine = AssetMachine.objects.create(
            name='Foreign Mixer', customer=cls.other_customer
        )
        cls.orphan = AssetMachine.objects.create(name='Orphan Mixer')

    def setUp(self):
        """Reset the scope table for each test."""
        _SCOPES.clear()
        _SCOPES['mc-actor'] = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        _SCOPES['mc-outsider'] = {
            MaintenanceScope(customer_id=self.other_customer.pk, site_key=None)
        }

    def _resolve(self, user=None, machine=None):
        """Resolve the machine context for an actor."""
        return context_service.resolve_context(
            user or self.actor,
            context_type='machine',
            object_id=str((machine or self.machine).pk),
        )

    def test_machine_context_resolves_with_a_qa_capability(self):
        """A machine pin is a reading surface, never a command surface."""
        context = self._resolve()
        self.assertEqual(context.context_type, 'machine')
        self.assertEqual(context.object_id, str(self.machine.pk))
        self.assertEqual(context.capabilities, (context_service.CAPABILITY_QA,))
        self.assertEqual(context.display_label, 'Mixer 2')
        self.assertTrue(context.token)

    def test_snapshot_is_identity_only(self):
        """A pin tells the model which asset it is on, not the whole record."""
        snapshot = self._resolve().snapshot
        self.assertEqual(
            set(snapshot), {'name', 'active', 'manufacturer', 'model', 'serial'}
        )

    def test_revision_does_not_assume_a_lifecycle_version(self):
        """AssetMachine has no lifecycle_version; the rail must not assume one.

        This is generated after authorization succeeds, so an unhandled record
        type would fail past every gate, inside a request about to succeed.
        """
        revision = context_service.source_revision_for(self.machine)
        self.assertTrue(revision.startswith('u:'))

    def test_out_of_scope_and_missing_are_indistinguishable(self):
        """Denial is scope-safe, including for the unreachable orphan."""
        for target in (self.other_machine, self.orphan):
            with self.subTest(machine=target.name):
                with self.assertRaises(context_service.ContextForbidden):
                    self._resolve(machine=target)
        with self.assertRaises(context_service.ContextForbidden):
            context_service.resolve_context(
                self.actor, context_type='machine', object_id='999999'
            )

    def test_every_gate_fails_closed(self):
        """Each switch independently makes the context unreachable."""
        for flag, value in (
            ('AIMMS_SCOPED_CHAT_ENABLED', False),
            ('AIMMS_SCOPED_CHAT_CONTEXTS', ['work_order']),
            ('AIMMS_ASSETS_ENABLED', False),
        ):
            with self.subTest(flag=flag):
                with self.settings(**{flag: value}):
                    with self.assertRaises(context_service.ContextTypeUnknown):
                        self._resolve()

    def test_unregistered_type_still_stays_unknown(self):
        """The settings list narrows what the code supports; it cannot add."""
        with self.settings(
            AIMMS_SCOPED_CHAT_CONTEXTS=['work_order', 'machine', 'packet']
        ):
            with self.assertRaises(context_service.ContextTypeUnknown):
                context_service.resolve_context(
                    self.actor, context_type='packet', object_id='1'
                )

    def test_reauthorize_agrees_with_resolve(self):
        """Both call sites read the same adapter table, so they cannot drift."""
        self.assertEqual(
            context_service.reauthorize_context(
                self.actor, context_type='machine', object_id=str(self.machine.pk)
            ),
            self.machine,
        )
        with self.assertRaises(context_service.ContextForbidden):
            context_service.reauthorize_context(
                self.outsider, context_type='machine', object_id=str(self.machine.pk)
            )

    def test_reauthorize_stops_when_the_type_is_switched_off(self):
        """A pin must not keep answering on yesterday's authority."""
        with self.settings(AIMMS_ASSETS_ENABLED=False):
            with self.assertRaises(context_service.ContextTypeUnknown):
                context_service.reauthorize_context(
                    self.actor, context_type='machine', object_id=str(self.machine.pk)
                )


@override_settings(**MACHINE_FLAGS)
class MachineToolTests(TestCase):
    """The machine tool registry: coverage, arguments and totality."""

    @classmethod
    def setUpTestData(cls):
        """Create one scoped asset and a conversation pinned to it."""
        cls.customer = Company.objects.create(name='MT Cust', is_customer=True)
        cls.actor = get_user_model().objects.create_superuser(
            username='mt-actor', email='mt@example.com', password='pw'
        )
        cls.machine = AssetMachine.objects.create(
            name='Press 1', customer=cls.customer
        )

    def setUp(self):
        """Reset scope and open a pinned conversation."""
        _SCOPES.clear()
        _SCOPES['mt-actor'] = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        context = context_service.resolve_context(
            self.actor, context_type='machine', object_id=str(self.machine.pk)
        )
        self.conversation = conversation_service.create_conversation(
            owner=self.actor, context=context
        )

    def _invoke(self, tool, arguments=None):
        """Invoke one scoped tool on the pinned machine."""
        return tool_service.invoke_tool(
            user=self.actor,
            conversation=self.conversation,
            tool_name=tool,
            arguments=arguments,
            turn_key=str(uuid.uuid4())[:32],
        )

    def test_every_machine_page_tab_has_a_tool(self):
        """The user asked for the whole page; the registry must cover it."""
        registered = set(tool_service.tools_for_context('machine'))
        self.assertEqual(
            registered,
            {
                'machine_summary',  # Details tab
                'machine_health',  # Health tab header
                'machine_signals',  # Health tab signal table
                'machine_signal_trend',  # Health tab sparkline
                'machine_anomalies',  # Health tab anomaly list
                'machine_installed_parts',  # Installed Parts tab
                'machine_maintenance_history',  # Maintenance tab
                'machine_attachments',  # Attachments tab
                # A selected controlled document rides the pin, whichever
                # record type the pin is - same tool the work-order pin has.
                'search_selected_controlled_document',
            },
        )

    def test_work_order_tools_are_not_reachable_from_a_machine_pin(self):
        """Registries are per context type, not a shared pool."""
        with self.assertRaises(tool_service.ToolNotAvailable):
            self._invoke('work_order_summary')

    def test_each_tool_returns_an_authorized_cited_envelope(self):
        """Every answer carries a citation and an as-of time."""
        for tool in tool_service.tools_for_context('machine'):
            if tool == 'search_selected_controlled_document':
                # The one tool whose success needs a conversation-level
                # document selection; its no-selection refusal is pinned in
                # its own test below.
                continue
            arguments = {'binding_id': 1} if tool == 'machine_signal_trend' else None
            with self.subTest(tool=tool):
                envelope = self._invoke(tool, arguments)
                self.assertTrue(envelope['authorized'], envelope)
                self.assertIsNone(envelope['error'])
                self.assertIsNotNone(envelope['result'])
                self.assertTrue(
                    ChatCitation.objects.filter(pk=envelope['citation_id']).exists()
                )

    def test_selected_document_tool_refuses_without_a_selection(self):
        """With no document selected, the envelope is a governed refusal.

        Mirrors the work-order pin's behaviour: the machine pin gets the same
        tool and the same fail-closed answer, not a different rule.
        """
        envelope = self._invoke(
            'search_selected_controlled_document', {'query': 'impeller wear'}
        )

        self.assertFalse(envelope['authorized'])
        self.assertEqual(envelope['error'], 'CONTROLLED_DOCUMENT_UNAVAILABLE')

    def test_arguments_are_typed_and_unknown_keys_refused(self):
        """A typed schema is what keeps a model from widening a read."""
        with self.assertRaises(tool_service.ToolArgumentsInvalid):
            self._invoke('machine_anomalies', {'limit': 'all'})
        with self.assertRaises(tool_service.ToolArgumentsInvalid):
            self._invoke('machine_anomalies', {'machine_id': 999})
        with self.assertRaises(tool_service.ToolArgumentsInvalid):
            self._invoke('machine_summary', {'limit': 5})
        with self.assertRaises(tool_service.ToolArgumentsInvalid):
            self._invoke('machine_signal_trend', {'hours': 24})

    def test_limits_are_clamped_not_trusted(self):
        """An out-of-range page size is refused rather than silently honoured."""
        with self.assertRaises(tool_service.ToolArgumentsInvalid):
            self._invoke('machine_anomalies', {'limit': 10_000})

    def test_a_raising_handler_becomes_an_envelope_not_a_500(self):
        """Totality is enforced at the rail, not trusted to every handler.

        The DRF view only catches ToolError, and this point is past a
        successful authorization -- the worst place to raise.
        """
        broken = tool_service.ToolSpec(
            name='machine_summary',
            version='1',
            description='deliberately broken',
            validate=tool_service._no_arguments,
            handler=lambda record, args, user: 1 / 0,
        )
        registry = dict(tool_service._REGISTRY['machine'])
        registry['machine_summary'] = broken
        with unittest.mock.patch.dict(
            tool_service._REGISTRY, {'machine': registry}
        ):
            envelope = self._invoke('machine_summary')
        self.assertIsNone(envelope['result'])
        self.assertEqual(envelope['error'], 'tool unavailable')
        self.assertIsNone(envelope['citation_id'])
        self.assertTrue(
            ChatToolInvocation.objects.filter(
                conversation=self.conversation,
                tool='machine_summary',
                authorization_result=ToolAuthorizationResult.DENIED,
            ).exists()
        )
