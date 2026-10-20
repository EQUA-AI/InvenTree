"""Native citation finalization/history coverage; scripted readiness is not provider health."""

import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import Client as DjangoClient
from django.test import TestCase, override_settings

import httpx
from asgiref.sync import async_to_sync

from ai.core import app as ai_app
from ai.core import runtime as runtime_module
from ai.core.agui.translate import SpecTranslator
from ai.core.auth import AIBoundaryAuthMiddleware, principal_for_user
from ai.core.config import get_settings
from ai.core.streaming import InMemoryEventEmitter
from ai.core.tools.capture_ledger import (
    ToolCaptureLedger,
    record_tool_result,
    tool_capture_ledger,
)
from ai.core.trusted_context import TrustedTurnContext
from ai.core.turn.finalize import persist_terminal
from ai.core.turn.state import TurnRun
from ai.core.turn_service import NormalizedTurnService
from aichat.models import ChatTurn
from aichat.services import ThreadRepository, canonical_request_fingerprint
from aichat.tests import test_evidence_media_stream as media


class _CitationFixture:
    """Qualified ordinary native source and actual persistence/authentication seams."""

    _PDF_B64 = media.EvidencePDFFramePolicyTests._PDF_B64
    _make_native_attachment = classmethod(
        media.EvidencePDFFramePolicyTests._make_native_attachment.__func__
    )

    @classmethod
    def setUpTestData(cls):
        """Reuse actual persisted grants, role, machines, indexed PDF and no provider."""
        media.EvidencePDFFramePolicyTests.setUpTestData.__func__(cls)

    def setUp(self):
        """Reuse the existing native middleware and ordinary-session control."""
        media.EvidencePDFFramePolicyTests.setUp(self)

    @staticmethod
    def _configured():
        """Opt into the tested manifest with a nonempty synthetic server scope only."""
        return get_settings().model_copy(
            update={
                'feature_media_evidence': True,
                'single_site_policy_key': 'site:native-citation-fixture',
            }
        )

    def _document(self, **overrides):
        """Server-shaped citation bound to this real synthetic indexed source."""
        citation = {
            'document': 'untrusted reference.pdf',
            'chunk_id': 'native-pdf-page2',
            'access_class': 'attachment_uploaded',
            'source_sha256': self.pdf_sha,
            'page_number': 2,
            'attachment_id': self.pdf_owned.pk,
            'model_type': 'assetmachine',
            'model_id': self.machine_owned.pk,
            'media_type': 'document',
        }
        citation.update(overrides)
        return citation

    def _persist(self, citations=None, *, suffix='one'):
        """Real ledger/media seam and terminal finalizer, stored by native repository."""
        citations = [self._document()] if citations is None else citations
        events = []
        with mock.patch('ai.core.config.get_settings', return_value=self._configured()):
            principal = principal_for_user(self.reader)
            context = TrustedTurnContext(
                actor=principal.actor,
                server_policy_key=principal.scope,
                server_policy_hash=hashlib.sha256(principal.scope.encode()).hexdigest(),
                thread_namespace='unscoped',
                server_route_hints=('/chat',),
                allowed_capabilities=('chat.unscoped.read',),
                correlation_id='native-citation-fixture',
                policy_version=principal.policy_version,
                untrusted_content='',
            )
            thread_id = f'thread_native_citation_{suffix}'
            repository = ThreadRepository(self.reader.pk, principal.scope)
            thread, _ = repository.get_or_create(thread_id)
            trusted = {'policy_version': principal.policy_version}
            content = 'What does the uploaded reference document report?'
            key = f'native-citation:{suffix}'
            fingerprint = canonical_request_fingerprint(
                content=content,
                modality='text',
                trusted_context=trusted,
                modality_metadata={},
            )
            begun = repository.begin_turn(
                thread_id,
                content=content,
                modality='text',
                trusted_context=trusted,
                modality_metadata={},
                idempotency_key=key,
                request_fingerprint=fingerprint,
                correlation_id=context.correlation_id,
            )
            run = TurnRun(
                actor=principal,
                trusted_context=context,
                content=content,
                modality='text',
                correlation_id=context.correlation_id,
                idempotency_key=key,
                server_pinned_workflow=None,
                server_generation_target=None,
                trusted=trusted,
                metadata={},
                repository=repository,
                thread=thread,
                turn=begun.turn,
            )
            service = NormalizedTurnService(workflow_factory=lambda: None)

            async def exercise():
                """Use actual event emission and restore the turn-local ledger in all exits."""
                emitter = InMemoryEventEmitter()

                async def observe(event):  # noqa: RUF029 - emitter handler protocol
                    events.append(event.to_dict())

                unsubscribe = await emitter.subscribe(SimpleNamespace(handle=observe))
                token = tool_capture_ledger.set(ToolCaptureLedger())
                try:
                    record_tool_result(
                        'documents.read:search_attachment_docs',
                        {'chunks': [{'citation': c} for c in citations]},
                    )
                    canonical = {
                        'thread_id': thread_id,
                        'turn_id': begun.turn.pk,
                        'message': 'Reference document only.',
                        'agent': 'fixture',
                        'workflow_used': 'wf8',
                        'response_state': 'complete',
                        'canonical_response': None,
                        'spoken_summary': '',
                        'reasoning_provenance': None,
                        'route': None,
                        'events': [],
                    }
                    canonical = await service._attach_media_evidence(
                        canonical,
                        thread_id=thread_id,
                        turn_id=begun.turn.pk,
                        emitter=emitter,
                    )
                    canonical['events'] = list(events)
                    return await persist_terminal(
                        service,
                        run,
                        canonical,
                        state='complete',
                        output_content=canonical['message'],
                    )
                finally:
                    tool_capture_ledger.reset(token)
                    unsubscribe()

            persisted = async_to_sync(exercise)()
        turn = ChatTurn.objects.select_related('output_message').get(pk=persisted.pk)
        return turn, events

    def _history(self, thread_id, *, actor=None, anonymous=False, ready=True):
        """Actual HTTP history/auth/ORM; real readiness gate with test-only initializer."""
        client = DjangoClient()
        headers = {}
        if not anonymous:
            client.force_login(actor or self.reader)
            headers['cookie'] = (
                f'{settings.SESSION_COOKIE_NAME}={client.cookies[settings.SESSION_COOKIE_NAME].value}'
            )

        async def fetch():
            """No principal, repository, route or readiness-dependency replacement."""
            fixture_runtime = runtime_module.RuntimeSupervisor()

            @asynccontextmanager
            async def initialize_fixture(_deadline):
                yield

            with mock.patch.object(runtime_module, 'runtime', fixture_runtime):
                transport = httpx.ASGITransport(
                    app=AIBoundaryAuthMiddleware(ai_app.app)
                )
                async with httpx.AsyncClient(
                    transport=transport, base_url='http://testserver'
                ) as http:
                    if ready:
                        fixture_runtime.start(initialize_fixture)
                        await fixture_runtime.wait_for_initial_result()
                        self.assertTrue(fixture_runtime.snapshot().available)
                    try:
                        return await http.get(f'/threads/{thread_id}', headers=headers)
                    finally:
                        await fixture_runtime.close()

        with mock.patch('ai.core.config.get_settings', return_value=self._configured()):
            return async_to_sync(fetch)()

    @staticmethod
    def _wire(events, thread_id):
        """Translate the actual captured live events through the production spec adapter."""
        translator = SpecTranslator(thread_id=thread_id, run_id='native-citation-wire')
        return [
            frame['value']['media_evidence']
            for event in events
            for frame in translator.translate(event)
            if frame.get('name') == 'aimms.mediaEvidence'
        ]

    @staticmethod
    def _source_url(entry):
        """Pin current requests to the source revision actually retained in the answer."""
        return (
            f'{media._url(entry["attachment_id"])}?revision={entry["source_revision"]}'
        )

    @staticmethod
    def _assistant(history):
        """Read the actual stored assistant message from owning HTTP history."""
        return [m for m in history.json()['messages'] if m['role'] == 'assistant'][-1]


@override_settings(
    MEDIA_ROOT=media._MEDIA_ROOT,
    X_FRAME_OPTIONS='DENY',
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='ungranted-pdf-fixture',
)
class AttachmentCitationPersistenceTests(_CitationFixture, TestCase):
    """Private qualification of the declared native fixture and every new tail."""

    def test_round_trip(self):
        """Stored canonical, metadata, actual session history and wire match."""
        turn, events = self._persist()
        entries = turn.canonical_result['media_evidence']
        self.assertEqual(turn.state, 'complete')
        self.assertEqual(entries[0]['source_revision'], self.pdf_sha)
        self.assertEqual(entries[0]['page_index'], 1)
        self.assertEqual(entries[0]['model_id'], self.machine_owned.pk)
        self.assertEqual(entries[0]['attachment_id'], self.pdf_owned.pk)
        self.assertEqual(turn.output_message.metadata['media_evidence'], entries)
        history = self._history(turn.thread_id)
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(self._assistant(history)['media_evidence'], entries)
        self.assertEqual(self._wire(events, turn.thread_id), [entries])
        response = self.client.get(self._source_url(entries[0]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(media._body(response), self.pdf_bytes)
        print(
            'LOCATOR_WITNESS:'
            + json.dumps({
                'canonical': entries,
                'metadata': turn.output_message.metadata['media_evidence'],
                'history': self._assistant(history)['media_evidence'],
                'wire': self._wire(events, turn.thread_id)[0],
            })
        )

    def test_foreign_history_denied(self):
        """Another real ordinary session cannot read the owning transcript."""
        turn, _ = self._persist()
        other = get_user_model().objects.create_user(
            username='citation-history-other', is_staff=False, is_superuser=False
        )
        response = self._history(turn.thread_id, actor=other)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(self.pdf_sha, response.text)

    def test_anonymous_history_denied(self):
        """The real native AI session boundary refuses unauthenticated reads."""
        turn, _ = self._persist()
        response = self._history(turn.thread_id, anonymous=True)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(self.pdf_sha, response.text)

    def test_unready_history_stays_unavailable(self):
        """The original readiness dependency remains a reached 503 fence."""
        turn, _ = self._persist()
        response = self._history(turn.thread_id, ready=False)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['detail']['code'], 'AI_RUNTIME_UNAVAILABLE')

    def test_legacy_no_invented_locator(self):
        """A legacy source lacking returned SHA/page remains honest after reload."""
        citation = self._document()
        citation.pop('source_sha256')
        citation.pop('page_number')
        turn, events = self._persist([citation])
        entries = turn.canonical_result['media_evidence']
        self.assertIsNone(entries[0]['source_revision'])
        self.assertIsNone(entries[0]['page_index'])
        self.assertEqual(turn.output_message.metadata['media_evidence'], entries)
        response = self._history(turn.thread_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._assistant(response)['media_evidence'], entries)
        self.assertEqual(self._wire(events, turn.thread_id), [entries])

    def test_revoked_source_keeps_historical_locator(self):
        """Revocation blocks current source reads, never rewrites historical metadata."""
        turn, _ = self._persist()
        entries = turn.canonical_result['media_evidence']
        self.grant_owned.delete()
        response = self.client.get(self._source_url(entries[0]))
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(self.pdf_sha, response.content.decode(errors='replace'))
        turn.refresh_from_db()
        self.assertEqual(turn.canonical_result['media_evidence'], entries)
        history = self._history(turn.thread_id)
        self.assertEqual(history.status_code, 200)
        self.assertEqual(self._assistant(history)['media_evidence'], entries)

    def test_replaced_source_keeps_historical_locator(self):
        """Changed stored bytes refuse the pinned revision while history stays literal."""
        turn, _ = self._persist()
        entries = turn.canonical_result['media_evidence']
        path = self.pdf_owned.attachment.name
        try:
            with default_storage.open(path, 'wb') as stream:
                stream.write(b'%PDF-replaced synthetic source')
            response = self.client.get(self._source_url(entries[0]))
            self.assertEqual(response.status_code, 404)
            turn.refresh_from_db()
            self.assertEqual(turn.canonical_result['media_evidence'], entries)
            history = self._history(turn.thread_id)
            self.assertEqual(history.status_code, 200)
            self.assertEqual(self._assistant(history)['media_evidence'], entries)
        finally:
            with default_storage.open(path, 'wb') as stream:
                stream.write(self.pdf_bytes)

    def test_stale_revision_refused_without_rewriting_history(self):
        """A wrong pinned revision refuses bytes without rewriting stored history."""
        turn, events = self._persist()
        entries = turn.canonical_result['media_evidence']
        entry = entries[0]
        self.assertEqual(entry['source_revision'], self.pdf_sha)
        self.assertEqual(entry['page_index'], 1)
        response = self.client.get(self._source_url(entry))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(media._body(response), self.pdf_bytes)
        wrong_revision = '0' * 64
        stale = self.client.get(
            f'{media._url(entry["attachment_id"])}?revision={wrong_revision}'
        )
        self.assertEqual(stale.status_code, 404)
        turn.refresh_from_db()
        self.assertEqual(turn.canonical_result['media_evidence'], entries)
        self.assertEqual(turn.output_message.metadata['media_evidence'], entries)
        history = self._history(turn.thread_id)
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(entries, self._assistant(history)['media_evidence'])
        self.assertEqual(self._wire(events, turn.thread_id), [entries])
