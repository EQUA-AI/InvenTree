"""AT-F1 native exact-target acceptance: real ORM authority only.

Real Attachment/Ingest/Chunk/User/RuleSet/scope records; only the embedding
and Search clients are recording fakes. Nothing about authorization is
mocked: the exact binding, role gates, client-code policy and final row
protection all run their production code paths.
"""

import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from tasks.scope import MaintenanceScope

from ai.core.integrations.attachment_corpus import (
    AttachmentRetrievalError,
    search_corpus_attachments,
)
from aichat.models import AttachmentIngest, AttachmentIngestState
from aichat.services.attachment_ingestion import derive_client_codes, run_ingest
from assets.models import AssetMachine, ClientScopeGrant
from common.models import Attachment
from users.models import RuleSet

from .test_attachment_rag_ingestion import (
    FakeEmbeddingClient,
    FakeProjection,
    RagFixtureTestCase,
    _ai_settings,
)

_GRANTS: dict[str, set[MaintenanceScope]] = {}

_MD = b'# Press Manual\n\n## Safety\n\nLock out power before service.\n'


def _grant_resolver(actor):
    """Resolver seam: return whatever the test granted this username."""
    return _GRANTS.get(actor.get_username(), set())


class FakeSearchClient:
    """Records every filter; returns scripted rows."""

    def __init__(self, rows=None):
        """Hold the scripted rows and the recorded call log."""
        self.filters: list[str] = []
        self.calls = 0
        self._rows = rows or []

    def search(self, **kwargs):
        """Return the scripted rows and record the call's filter."""
        self.calls += 1
        self.filters.append(kwargs['filter'])
        return list(self._rows)


class RecordingEmbeddingClient:
    """Recording fake embedding client (external provider seam)."""

    def __init__(self):
        """Start with an empty embedded-query record."""
        self.queries: list[str] = []

    def embed_query(self, text):
        """Record the query and answer with a fixed fake vector."""
        self.queries.append(text)
        return [0.5] * 1536


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER=f'{__name__}._grant_resolver',
    AIMMS_MACHINE_AI_READ_ENABLED=True,
    AIMMS_ATTACHMENT_RAG_ENABLED=True,
)
class ExactAttachmentTargetTests(RagFixtureTestCase):
    """AT-R01/R02/R04/R07/R09/R10 native cases (AT-F1)."""

    @classmethod
    def setUpTestData(cls):
        """Shared owner fixtures plus one ordinary reader user."""
        super().setUpTestData()
        cls.reader = get_user_model().objects.create_user(
            username='exact-reader', password='x'
        )

    def setUp(self):
        """Acme-only scope, real roles, and one ingested machine attachment."""
        super().setUp()
        self.enterContext(mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '0'}))
        _GRANTS.clear()
        _GRANTS[self.reader.get_username()] = {
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.client_acme.pk
            )
        }
        group = Group.objects.create(name=f'exact-{self._testMethodName}')
        self.reader.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        RuleSet.objects.update_or_create(
            group=group, name='part', defaults={'can_view': True}
        )
        self.attachment = Attachment.objects.create(
            model_type='assetmachine',
            model_id=self.machine.pk,
            attachment=SimpleUploadedFile('exact-target.md', _MD),
            comment='exact target',
        )
        self.ingest = self._ingest(self.attachment)

    # -- fixtures -----------------------------------------------------------

    def _ingest(self, attachment):
        """Real doc-pipeline ingest under a valid AI config and fakes."""
        ai_settings = _ai_settings(FEATURE_ATTACHMENT_RAG_INGEST=True)
        with mock.patch('ai.core.config.get_settings', return_value=ai_settings):
            run_ingest(
                attachment.pk,
                embedding_client=FakeEmbeddingClient(),
                projection=FakeProjection(),
            )
        latest = AttachmentIngest.objects.filter(attachment_id=attachment.pk)
        return latest.order_by('-pk').first()

    def _row_for(self, attachment, ingest, **overrides):
        """One Search row stamped exactly like the native projection would."""
        chunk_ids = list(ingest.chunks.values_list('search_doc_id', flat=True))
        row = {
            'id': chunk_ids[0] if chunk_ids else f'att-{attachment.pk}-c0',
            'attachment_id': attachment.pk,
            'model_type': attachment.model_type,
            'model_id': attachment.model_id,
            'part_id': None,
            'part_name': '',
            'asset_id': '',
            'machine_name': '',
            'doc_type': 'manual',
            'source_file_name': 'exact-target.md',
            'section_path': 'Safety',
            'heading_1': 'Safety',
            'heading_2': '',
            'heading_3': '',
            'page_number': 1,
            'content': 'Lock out power before service.',
            'as_of': '2026-10-17',
            'access_class': 'attachment_uploaded',
            'client_codes': list(
                derive_client_codes(attachment.model_type, attachment.model_id)
            ),
            'scope_key': 'site-a',
            'source_sha256': ingest.source_sha256,
            'is_current': True,
            '@search.score': 2.0,
        }
        row.update(overrides)
        return row

    def _search(self, *, rows=None, attachment_id=None, user=None, **kwargs):
        """Run the corpus with recording fakes under valid retrieval config."""
        search_client = kwargs.pop('search_client', None) or FakeSearchClient(rows=rows)
        embedder = kwargs.pop('embedding_client', None) or RecordingEmbeddingClient()
        ai_settings = _ai_settings(FEATURE_ATTACHMENT_RAG_RETRIEVAL=True)
        with mock.patch('ai.core.config.get_settings', return_value=ai_settings):
            result = search_corpus_attachments(
                user=user or self.reader,
                query='What does the manual say about safety?',
                attachment_id=attachment_id,
                search_client=search_client,
                embedding_client=embedder,
                **kwargs,
            )
        return search_client, embedder, result

    # -- AT-R01/R03: exact binding beats misleading hints -------------------

    def test_exact_machine_target_ignores_misleading_part_hint_and_type(self):
        """R01/R03: misleading hints never run a resolver or narrow the target."""
        calls: list[str] = []

        def _spy(_actor, name):
            calls.append(name)
            return [{'part_id': self.part.pk, 'name': self.part.name, 'ipn': ''}]

        search, _embed, result = self._search(
            rows=[self._row_for(self.attachment, self.ingest)],
            attachment_id=self.attachment.pk,
            part='Seal Kit',
            doc_type='datasheet',
            part_resolver=_spy,
            machine_resolver=_spy,
        )
        self.assertEqual(calls, [])
        self.assertEqual(search.calls, 1)
        filter_expression = search.filters[0]
        self.assertIn(f'attachment_id eq {self.attachment.pk}', filter_expression)
        self.assertIn("model_type eq 'assetmachine'", filter_expression)
        self.assertIn(f'model_id eq {self.machine.pk}', filter_expression)
        self.assertNotIn('part_id', filter_expression)
        self.assertNotIn('doc_type', filter_expression)
        self.assertEqual(result['attachment_filter'], 'exact')
        for chunk in result['chunks']:
            self.assertEqual(chunk['citation']['attachment_id'], self.attachment.pk)

    # -- AT-R04/R07: one indistinguishable, value-free refusal --------------

    def test_unavailable_targets_refuse_identically_without_provider_work(self):
        """R04/R07: missing/foreign/unindexed/wrong-index refuse identically."""
        foreign = Attachment.objects.create(
            model_type='assetmachine',
            model_id=self.machine_zeta.pk,
            attachment=SimpleUploadedFile('foreign.md', _MD),
            comment='foreign owner',
        )
        foreign_ingest = self._ingest(foreign)
        self.assertIsNotNone(foreign_ingest)

        unindexed = Attachment.objects.create(
            model_type='assetmachine',
            model_id=self.machine.pk,
            attachment=SimpleUploadedFile('unindexed.md', _MD),
            comment='unindexed',
        )
        unindexed_ingest = self._ingest(unindexed)
        AttachmentIngest.objects.filter(pk=unindexed_ingest.pk).update(state='pending')

        wrong_index = Attachment.objects.create(
            model_type='assetmachine',
            model_id=self.machine.pk,
            attachment=SimpleUploadedFile('wrong-index.md', _MD),
            comment='wrong index',
        )
        wrong_ingest = self._ingest(wrong_index)
        AttachmentIngest.objects.filter(pk=wrong_ingest.pk).update(
            search_index_name='some-other-index'
        )

        failures = []
        for label, target_id in (
            ('missing', 999999),
            ('cross_client', foreign.pk),
            ('unindexed', unindexed.pk),
            ('wrong_index', wrong_index.pk),
        ):
            search = FakeSearchClient(rows=[])
            embed = RecordingEmbeddingClient()
            with self.assertRaises(AttachmentRetrievalError) as raised:
                self._search(
                    rows=[],
                    attachment_id=target_id,
                    search_client=search,
                    embedding_client=embed,
                )
            self.assertEqual(
                raised.exception.code, 'ATTACHMENT_TARGET_UNAVAILABLE', label
            )
            failures.append(str(raised.exception))
            self.assertEqual(embed.queries, [], label)
            self.assertEqual(search.calls, 0, label)
        self.assertEqual(len(set(failures)), 1, failures)
        for message in failures:
            self.assertNotIn('999999', message)

    # -- AT-R09/R08: returned rows must match the bound target --------------

    def test_foreign_rows_from_noncompliant_search_are_rejected(self):
        """R09: a noncompliant Search client cannot surface foreign rows."""
        foreign = Attachment.objects.create(
            model_type='assetmachine',
            model_id=self.machine_zeta.pk,
            attachment=SimpleUploadedFile('smuggled.md', _MD),
            comment='smuggled',
        )
        foreign_ingest = self._ingest(foreign)
        _search, _embed, result = self._search(
            rows=[self._row_for(foreign, foreign_ingest)],
            attachment_id=self.attachment.pk,
        )
        self.assertEqual(result['chunks'], [])

    def test_row_with_tampered_hash_is_rejected_before_exposure(self):
        """R08: a wrong source hash on a row never reaches exposure."""
        tampered = self._row_for(
            self.attachment, self.ingest, id='att-tampered-c0', source_sha256='b' * 64
        )
        good = self._row_for(self.attachment, self.ingest)
        expected_chunk_id = good['id']
        _search, _embed, result = self._search(
            rows=[tampered, good], attachment_id=self.attachment.pk
        )
        self.assertEqual(
            [chunk['citation']['chunk_id'] for chunk in result['chunks']],
            [expected_chunk_id],
        )

    # -- AT-R02: part-owned exact target keeps the native client policy ------

    def test_part_exact_target_requires_part_role_and_client_policy(self):
        """R02: part-owned targets keep the part role and client policy."""
        part_attachment = Attachment.objects.create(
            model_type='part',
            model_id=self.part.pk,
            attachment=SimpleUploadedFile('part-target.md', _MD),
            comment='part target',
        )
        part_ingest = self._ingest(part_attachment)
        row = self._row_for(part_attachment, part_ingest)
        _search, _embed, result = self._search(
            rows=[row], attachment_id=part_attachment.pk
        )
        self.assertEqual(len(result['chunks']), 1)

        # work_order-only reader: the part arm's own role is mandatory.
        RuleSet.objects.filter(group__user=self.reader, name='part').update(
            can_view=False
        )
        with self.assertRaises(AttachmentRetrievalError) as raised:
            self._search(rows=[row], attachment_id=part_attachment.pk)
        self.assertEqual(raised.exception.code, 'ATTACHMENT_TARGET_UNAVAILABLE')

    def test_linked_clientless_part_target_is_denied(self):
        """R02: linked-but-clientless parts fail closed (no 'internal' shortcut)."""
        from assets.models import MachinePart

        MachinePart.objects.create(
            machine=self.machine_unscoped, part=self.part_unlinked
        )
        self.assertEqual(derive_client_codes('part', self.part_unlinked.pk), [])
        denied = Attachment.objects.create(
            model_type='part',
            model_id=self.part_unlinked.pk,
            attachment=SimpleUploadedFile('denied-part.md', _MD),
            comment='linked clientless',
        )
        ingest = self._ingest(denied)
        with self.assertRaises(AttachmentRetrievalError) as raised:
            self._search(rows=[self._row_for(denied, ingest)], attachment_id=denied.pk)
        self.assertEqual(raised.exception.code, 'ATTACHMENT_TARGET_UNAVAILABLE')

    # -- AT-R10: malformed selectors refuse before any provider work --------

    def test_invalid_selector_refuses_before_network(self):
        """R10: bool/zero/negative/fraction/string selectors refuse pre-network."""
        for bad in (True, 0, -1, 2.0, '2'):
            search = FakeSearchClient(rows=[])
            embed = RecordingEmbeddingClient()
            with self.assertRaises(AttachmentRetrievalError) as raised:
                self._search(
                    rows=[],
                    attachment_id=bad,
                    search_client=search,
                    embedding_client=embed,
                )
            self.assertEqual(raised.exception.code, 'ATTACHMENT_TARGET_INVALID')
            self.assertEqual(embed.queries, [])
            self.assertEqual(search.calls, 0)

    # -- AT-R07/R08: native ingest profile and owner coherence -------------
    def _readable_control_row(self):
        return self._row_for(self.attachment, self.ingest, asset_id=self.machine.serial)

    def _assert_control_readable(self):
        _search, _embed, result = self._search(
            rows=[self._readable_control_row()], attachment_id=self.attachment.pk
        )
        self.assertEqual(len(result['chunks']), 1)

    def _assert_refused_before_provider(self):
        search = FakeSearchClient(rows=[self._readable_control_row()])
        embed = RecordingEmbeddingClient()
        with self.assertRaises(AttachmentRetrievalError) as raised:
            self._search(
                rows=[],
                attachment_id=self.attachment.pk,
                search_client=search,
                embedding_client=embed,
            )
        self.assertEqual(raised.exception.code, 'ATTACHMENT_TARGET_UNAVAILABLE')
        self.assertEqual(search.calls, 0)
        self.assertEqual(embed.queries, [])

    def test_stale_native_profile_refuses_before_provider(self):
        """R07/R08: a stale ingest profile cannot be rehabilitated by a row."""
        self._assert_control_readable()
        AttachmentIngest.objects.filter(pk=self.ingest.pk).update(
            embedding_profile='stale-profile'
        )
        self._assert_refused_before_provider()

    def test_stale_ingest_owner_refuses_before_provider(self):
        """R07/R08: a stored ingest owner must match the current Attachment."""
        self._assert_control_readable()
        AttachmentIngest.objects.filter(pk=self.ingest.pk).update(
            model_id=self.machine_zeta.pk
        )
        self._assert_refused_before_provider()

    def test_final_checker_rejects_current_owner_change(self):
        """R08: the last exposure check compares ownership, never just authorizes it."""
        from ai.core.integrations import retrieval_authority as authority

        self._assert_control_readable()
        settings = _ai_settings(FEATURE_ATTACHMENT_RAG_RETRIEVAL=True)
        with mock.patch('ai.core.config.get_settings', return_value=settings):
            target = authority.exact_attachment_target(self.reader, self.attachment.pk)
            self.assertIsNotNone(target)
            row = self._readable_control_row()
            self.assertEqual(
                authority.exact_target_row_reason(row, target=target, user=self.reader),
                '',
            )
            replacement = AssetMachine.objects.create(
                name='Other authorized owner',
                client=self.client_acme,
                serial='OTHER-OWNED',
            )
            self.assertNotEqual(replacement.pk, self.machine.pk)
            type(self.attachment).objects.filter(pk=self.attachment.pk).update(
                model_id=replacement.pk
            )
            self.assertNotEqual(
                authority.exact_target_row_reason(row, target=target, user=self.reader),
                '',
            )

    # -- AT-R05: real persisted grants, before and during provider work -----
    @override_settings(
        AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
        AIMMS_SINGLE_SITE_CLIENT_CODE='ungranted-fallback-client',
    )
    def test_persisted_grant_revocation_refuses_before_provider(self):
        """Positive negative-control: pre-provider revocation already refuses."""
        ClientScopeGrant.objects.create(user=self.reader, client=self.client_acme)
        ClientScopeGrant.objects.create(user=self.reader, client=self.client_zeta)
        self._assert_control_readable()
        ClientScopeGrant.objects.filter(
            user=self.reader, client=self.client_acme
        ).delete()
        self._assert_refused_before_provider()

    @override_settings(
        AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
        AIMMS_SINGLE_SITE_CLIENT_CODE='ungranted-fallback-client',
    )
    def test_persisted_grant_revocation_during_provider_is_unavailable(self):
        """R05: mid-provider revocation is unavailable, not 'no passages'."""
        ClientScopeGrant.objects.create(user=self.reader, client=self.client_acme)
        ClientScopeGrant.objects.create(user=self.reader, client=self.client_zeta)
        self._assert_control_readable()
        reader, client = self.reader, self.client_acme

        class RevokingSearch(FakeSearchClient):
            def search(self, **kwargs):
                rows = super().search(**kwargs)
                ClientScopeGrant.objects.filter(user=reader, client=client).delete()
                return rows

        search = RevokingSearch(rows=[self._readable_control_row()])
        with self.assertRaises(AttachmentRetrievalError) as raised:
            self._search(attachment_id=self.attachment.pk, search_client=search)
        self.assertEqual(raised.exception.code, 'ATTACHMENT_TARGET_UNAVAILABLE')
        self.assertEqual(search.calls, 1)

    # -- AT-R08/R09: row integrity and same-owner substitution -------------
    def test_row_integrity_isolates_each_bound_field(self):
        """Wrong hash/owner/profile reject one field at a time, valid ids retained."""
        self._assert_control_readable()
        valid_id = self._readable_control_row()['id']
        tampered = self._row_for(self.attachment, self.ingest, source_sha256='b' * 64)
        self.assertEqual(tampered['id'], valid_id)
        _s, _e, result = self._search(rows=[tampered], attachment_id=self.attachment.pk)
        self.assertEqual(result['chunks'], [])
        wrong_owner = self._row_for(
            self.attachment, self.ingest, model_id=self.machine_zeta.pk
        )
        self.assertEqual(wrong_owner['id'], valid_id)
        _s, _e, result = self._search(
            rows=[wrong_owner], attachment_id=self.attachment.pk
        )
        self.assertEqual(result['chunks'], [])
        wrong_profile = self._row_for(
            self.attachment, self.ingest, embedding_profile='stale-profile'
        )
        _s, _e, result = self._search(
            rows=[wrong_profile], attachment_id=self.attachment.pk
        )
        self.assertEqual(result['chunks'], [])
        good = self._row_for(self.attachment, self.ingest)
        _s, _e, result = self._search(
            rows=[tampered, wrong_owner, wrong_profile, good],
            attachment_id=self.attachment.pk,
        )
        self.assertEqual(
            [c['citation']['chunk_id'] for c in result['chunks']], [valid_id]
        )

    def test_same_owner_substitution_rejected_while_omission_reads_other(self):
        """R09: exact selection never surfaces another same-owner source."""
        other = Attachment.objects.create(
            model_type='assetmachine',
            model_id=self.machine.pk,
            attachment=SimpleUploadedFile('other-source.md', _MD),
            comment='same owner other source',
        )
        other_ingest = self._ingest(other)
        other_row = self._row_for(other, other_ingest)
        # Selector omission: the same owner's other source is legitimately readable.
        _s, _e, result = self._search(rows=[other_row])
        self.assertTrue(result['chunks'])
        self.assertEqual(
            [c['citation']['attachment_id'] for c in result['chunks']], [other.pk]
        )
        # Exact selector: a noncompliant Search cannot smuggle that source in.
        _s, _e, result = self._search(
            rows=[other_row], attachment_id=self.attachment.pk
        )
        self.assertEqual(result['chunks'], [])

    def test_exact_citations_preserve_identity_and_fences(self):
        """Citations keep native identity; free text stays fenced."""
        from ai.core.tools.diagnostics import fence_untrusted_content

        self._assert_control_readable()
        _s, _e, result = self._search(
            rows=[self._readable_control_row()], attachment_id=self.attachment.pk
        )
        self.assertTrue(result['chunks'])
        citation = result['chunks'][0]['citation']
        self.assertEqual(citation['attachment_id'], self.attachment.pk)
        self.assertEqual(citation['model_type'], 'assetmachine')
        self.assertEqual(citation['model_id'], self.machine.pk)
        self.assertEqual(citation['source_sha256'], self.ingest.source_sha256)
        self.assertEqual(citation['page_number'], 1)
        self.assertEqual(citation['access_class'], 'attachment_uploaded')
        self.assertEqual(
            result['chunks'][0]['excerpt'],
            fence_untrusted_content('Lock out power before service.'),
        )
        self.assertEqual(citation['document'], fence_untrusted_content('exact-target'))
        self.assertEqual(
            citation['source_file_name'], fence_untrusted_content('exact-target.md')
        )

    # -- AT-R07: unavailable sources stay indistinguishable -----------------
    def test_unavailable_states_and_unsupported_owner_stay_indistinguishable(self):
        """Deleted/superseded ledger states and unsupported owners refuse alike."""
        self._assert_control_readable()
        outcomes = {}
        for label in ('missing', 'superseded', 'deleted', 'unsupported_owner'):
            if label == 'missing':
                target_id = 999999
            elif label == 'superseded':
                AttachmentIngest.objects.filter(pk=self.ingest.pk).update(
                    state=AttachmentIngestState.SUPERSEDED
                )
                target_id = self.attachment.pk
            elif label == 'deleted':
                AttachmentIngest.objects.filter(pk=self.ingest.pk).update(
                    state=AttachmentIngestState.DELETED
                )
                target_id = self.attachment.pk
            else:
                unsupported = Attachment.objects.create(
                    model_type='workorder',
                    model_id=1,
                    attachment=SimpleUploadedFile('wo.md', _MD),
                    comment='unsupported owner',
                )
                target_id = unsupported.pk
            search = FakeSearchClient(rows=[])
            embed = RecordingEmbeddingClient()
            with self.assertRaises(AttachmentRetrievalError) as raised:
                self._search(
                    rows=[],
                    attachment_id=target_id,
                    search_client=search,
                    embedding_client=embed,
                )
            self.assertEqual(
                raised.exception.code, 'ATTACHMENT_TARGET_UNAVAILABLE', label
            )
            self.assertEqual(search.calls, 0, label)
            self.assertEqual(embed.queries, [], label)
            outcomes[label] = str(raised.exception)
        self.assertEqual(len(set(outcomes.values())), 1, outcomes)
        for message in outcomes.values():
            self.assertNotIn('999999', message)

    # -- AT-F1 verification pass 3: frozen-implementation regression ---------

    def _verification_scope_context(self, machine_ids, machine_serials):
        """Bind a REAL enforced TurnScopeContext to the live ContextVar."""
        from ai.core.analysis.scope import MODE_EXPLICIT
        from ai.core.analysis.scope_context import TurnScopeContext, turn_scope_context

        context = TurnScopeContext(
            mode=MODE_EXPLICIT,
            machine_ids=frozenset(machine_ids),
            machine_serials=frozenset(machine_serials),
            date_from=None,
            date_to=None,
            source_classes=frozenset(),
            scope_hash='verification-scope-hash',
            scope_version=1,
            snapshot_id='snap_verification_3',
            thread_pk=None,
            display_label='verification scope',
            shadow=False,
            enforce=True,
        )
        turn_scope_context.set(context)
        return context

    def _verification_set_role(self, active):
        """Persist the reader's work_order view role on or off."""
        RuleSet.objects.filter(group__user=self.reader, name='work_order').update(
            can_view=active
        )

    def test_verification_enforced_scope_identity_and_serial_floor(self):
        """AT-R06: id membership and the serial floor refuse separately."""
        from ai.core.analysis.scope_context import turn_scope_context

        token = turn_scope_context.set(None)
        self.addCleanup(turn_scope_context.reset, token)
        self._assert_control_readable()
        twin = AssetMachine.objects.create(
            name='Twin Press', client=self.client_acme, serial='SN-TWIN'
        )
        # Same serial as the target, different same-client native id: the
        # identity leg alone must refuse before any provider call.
        self._verification_scope_context([twin.pk], [self.machine.serial])
        search = FakeSearchClient(rows=[self._readable_control_row()])
        embed = RecordingEmbeddingClient()
        with self.assertRaises(AttachmentRetrievalError) as raised:
            self._search(
                rows=[],
                attachment_id=self.attachment.pk,
                search_client=search,
                embedding_client=embed,
            )
        self.assertEqual(raised.exception.code, 'ATTACHMENT_TARGET_UNAVAILABLE')
        self.assertEqual(search.calls, 0)
        self.assertEqual(embed.queries, [])
        # Allowed native id AND matching serial: nonempty result, serial floor.
        self._verification_scope_context([self.machine.pk], [self.machine.serial])
        scoped_search, _embed, result = self._search(
            rows=[self._readable_control_row()], attachment_id=self.attachment.pk
        )
        self.assertEqual(len(result['chunks']), 1)
        self.assertIn(
            f"search.in(asset_id, '{self.machine.serial}'", scoped_search.filters[0]
        )
        # Allowed native id, different nonempty serial: the serial leg alone
        # must refuse before any provider call.
        self._verification_scope_context([self.machine.pk], ['SN-OTHER-SERIAL'])
        search = FakeSearchClient(rows=[self._readable_control_row()])
        embed = RecordingEmbeddingClient()
        with self.assertRaises(AttachmentRetrievalError) as raised:
            self._search(
                rows=[],
                attachment_id=self.attachment.pk,
                search_client=search,
                embedding_client=embed,
            )
        self.assertEqual(raised.exception.code, 'ATTACHMENT_TARGET_UNAVAILABLE')
        self.assertEqual(search.calls, 0)
        self.assertEqual(embed.queries, [])

    def test_verification_empty_floor_refuses_serial_stamped_target(self):
        """AT-R06: an explicit empty floor never admits a stamped target."""
        self._assert_control_readable()
        search = FakeSearchClient(rows=[self._readable_control_row()])
        embed = RecordingEmbeddingClient()
        with self.assertRaises(AttachmentRetrievalError) as raised:
            self._search(
                rows=[],
                attachment_id=self.attachment.pk,
                scope_asset_ids=(),
                search_client=search,
                embedding_client=embed,
            )
        self.assertEqual(raised.exception.code, 'ATTACHMENT_TARGET_UNAVAILABLE')
        self.assertEqual(search.calls, 0)
        self.assertEqual(embed.queries, [])
        self._assert_control_readable()

    def test_verification_role_revocation_before_and_during_provider(self):
        """AT-R05: persisted role revocation refuses before and during."""
        self._assert_control_readable()
        set_role = self._verification_set_role

        class RevokingRoles(FakeSearchClient):
            """Search callback persisting a work_order role revocation."""

            def search(self, **kwargs):
                """Return the scripted rows after revoking the role."""
                rows = super().search(**kwargs)
                set_role(False)
                return rows

        search = RevokingRoles(rows=[self._readable_control_row()])
        with self.assertRaises(AttachmentRetrievalError) as raised:
            self._search(attachment_id=self.attachment.pk, search_client=search)
        self.assertEqual(raised.exception.code, 'ATTACHMENT_TARGET_UNAVAILABLE')
        self.assertEqual(search.calls, 1)
        set_role(True)
        self._assert_control_readable()
        set_role(False)
        self._assert_refused_before_provider()

    def test_verification_account_deactivation_before_and_during_provider(self):
        """AT-R05: real is_active=False refuses before and during."""
        self._assert_control_readable()
        user_model = get_user_model()
        reader_pk = self.reader.pk

        class Deactivating(FakeSearchClient):
            """Search callback persisting the reader's deactivation."""

            def search(self, **kwargs):
                """Return the scripted rows after erasing the account."""
                rows = super().search(**kwargs)
                user_model.objects.filter(pk=reader_pk).update(is_active=False)
                return rows

        search = Deactivating(rows=[self._readable_control_row()])
        with self.assertRaises(AttachmentRetrievalError) as raised:
            self._search(attachment_id=self.attachment.pk, search_client=search)
        self.assertEqual(raised.exception.code, 'ATTACHMENT_TARGET_UNAVAILABLE')
        self.assertEqual(search.calls, 1)
        user_model.objects.filter(pk=reader_pk).update(is_active=True)
        self._assert_control_readable()
        user_model.objects.filter(pk=reader_pk).update(is_active=False)
        self._assert_refused_before_provider()

    def test_verification_unlinked_part_internal_client_reads(self):
        """AT-R02: unlinked Part reads under the real active internal client."""
        from assets.models import Client

        self.assertEqual(
            derive_client_codes('part', self.part_unlinked.pk), ['internal']
        )
        # The preserved native test DB already carries the real internal
        # client row; reuse it when present, else create the active client.
        internal, _created = Client.objects.get_or_create(
            code='internal', defaults={'name': 'Internal (verification-3)'}
        )
        Client.objects.filter(pk=internal.pk, active=False).update(active=True)
        _GRANTS[self.reader.get_username()] = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=internal.pk)
        }
        part_attachment = Attachment.objects.create(
            model_type='part',
            model_id=self.part_unlinked.pk,
            attachment=SimpleUploadedFile('unlinked-internal.md', _MD),
            comment='unlinked internal part',
        )
        ingest = self._ingest(part_attachment)
        row = self._row_for(part_attachment, ingest)
        self.assertEqual(row['client_codes'], ['internal'])
        _s, _e, result = self._search(rows=[row], attachment_id=part_attachment.pk)
        self.assertEqual(len(result['chunks']), 1)
        citation = result['chunks'][0]['citation']
        self.assertEqual(citation['attachment_id'], part_attachment.pk)
        self.assertEqual(citation['model_type'], 'part')
        self.assertEqual(citation['model_id'], self.part_unlinked.pk)

    def test_verification_deleted_attachment_row_refuses_before_provider(self):
        """AT-R07: a real Attachment-row deletion stays a unified refusal."""
        self._assert_control_readable()
        saved_id = self.attachment.pk
        # Native ORM delete and signals run normally; the shared fixture's
        # own background-offload seam is the only stub.
        with mock.patch('InvenTree.tasks.offload_task', return_value=True):
            self.attachment.delete()
        self.assertFalse(Attachment.objects.filter(pk=saved_id).exists())
        search = FakeSearchClient(rows=[])
        embed = RecordingEmbeddingClient()
        with self.assertRaises(AttachmentRetrievalError) as raised:
            self._search(
                rows=[],
                attachment_id=saved_id,
                search_client=search,
                embedding_client=embed,
            )
        self.assertEqual(raised.exception.code, 'ATTACHMENT_TARGET_UNAVAILABLE')
        self.assertEqual(search.calls, 0)
        self.assertEqual(embed.queries, [])
        self.assertNotIn(str(saved_id), str(raised.exception))

    def test_verification_native_diagnostics_meta_shapes(self):
        """Native diagnostics: validated ids, flag-only hints, real statuses."""
        import json

        from ai.core.correlation import bind_correlation
        from ai.core.tools.capture_ledger import bind_tool_captures, tool_capture_ledger

        token = tool_capture_ledger.set(None)
        self.addCleanup(tool_capture_ledger.reset, token)
        bind_correlation('corr-verification-3')
        self.addCleanup(bind_correlation, '')

        def _target_metas(ledger):
            """The exact-target diagnostic meta entries of one call."""
            return [
                entry for entry in ledger.retrieval_metas() if 'ignored_hints' in entry
            ]

        # Successful exact retrieval with misleading hints.
        ledger = bind_tool_captures()
        _s, _e, result = self._search(
            rows=[self._readable_control_row()],
            attachment_id=self.attachment.pk,
            part='Seal Kit',
            doc_type='datasheet',
        )
        self.assertEqual(len(result['chunks']), 1)
        self.assertEqual(result['attachment_filter'], 'exact')
        metas = _target_metas(ledger)
        self.assertEqual(len(metas), 1)
        meta = metas[0]
        self.assertEqual(meta['correlation_id'], 'corr-verification-3')
        self.assertEqual(meta['target_selector'], 'attachment_id')
        self.assertEqual(meta['target_filter'], 'exact')
        self.assertEqual(meta['returned_count'], 1)
        self.assertEqual(meta['refusal_status'], 'ok')
        self.assertEqual(meta['ignored_hints'], ['part', 'doc_type'])
        self.assertEqual(meta['requested_attachment_id'], self.attachment.pk)
        self.assertEqual(meta['effective_attachment_id'], self.attachment.pk)
        self.assertEqual(meta['effective_owner_type'], 'assetmachine')
        self.assertEqual(meta['effective_owner_id'], self.machine.pk)
        serialized = json.dumps(meta)
        self.assertNotIn('Seal Kit', serialized)
        self.assertNotIn('datasheet', serialized)
        self.assertNotIn('SN-100', serialized)
        outward = json.dumps(result)
        for protected in (
            'corr-verification-3',
            'ignored_hints',
            'scope_hash',
            'client_codes',
        ):
            self.assertNotIn(protected, outward)
        # Unavailable id: the caller's validated requested id only.
        ledger = bind_tool_captures()
        with self.assertRaises(AttachmentRetrievalError) as raised:
            self._search(rows=[], attachment_id=424242)
        self.assertEqual(raised.exception.code, 'ATTACHMENT_TARGET_UNAVAILABLE')
        metas = _target_metas(ledger)
        self.assertEqual(len(metas), 1)
        meta = metas[0]
        self.assertEqual(meta['refusal_status'], 'target_unavailable')
        self.assertEqual(meta['requested_attachment_id'], 424242)
        self.assertNotIn('effective_attachment_id', meta)
        self.assertNotIn('effective_owner_type', meta)
        self.assertNotIn('effective_owner_id', meta)
        self.assertNotIn('SN-100', json.dumps(meta))
        self.assertNotIn('Lock out', json.dumps(meta))
        # Readable target, zero provider rows: separately no_passages.
        ledger = bind_tool_captures()
        _s, _e, result = self._search(rows=[], attachment_id=self.attachment.pk)
        self.assertEqual(result['chunks'], [])
        self.assertEqual(result['returned_count'], 0)
        metas = _target_metas(ledger)
        self.assertEqual(len(metas), 1)
        meta = metas[0]
        self.assertEqual(meta['refusal_status'], 'no_passages')
        self.assertEqual(meta['returned_count'], 0)
        self.assertEqual(meta['effective_attachment_id'], self.attachment.pk)
        # Invalid raw selector is never echoed anywhere.
        ledger = bind_tool_captures()
        raw = 'RAW-SELECTOR-ECHO-PROBE'
        with self.assertRaises(AttachmentRetrievalError) as raised:
            self._search(rows=[], attachment_id=raw)
        self.assertEqual(raised.exception.code, 'ATTACHMENT_TARGET_INVALID')
        metas = _target_metas(ledger)
        self.assertEqual(len(metas), 1)
        meta = metas[0]
        self.assertEqual(meta['refusal_status'], 'invalid_selector')
        self.assertNotIn('requested_attachment_id', meta)
        self.assertNotIn('effective_attachment_id', meta)
        self.assertNotIn(raw, json.dumps(meta))
        self.assertNotIn(raw, str(raised.exception))
        for entry in ledger.retrieval_metas():
            self.assertNotIn(raw, json.dumps(entry))
