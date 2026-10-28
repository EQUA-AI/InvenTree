"""Offline manual retrieval enforces the native registry and machine boundary."""

import hashlib
import json
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from tasks.scope import MaintenanceScope

from ai.core.integrations.controlled_document_corpus import search_corpus
from ai.core.integrations.local_controlled_documents import search_local_manuals
from aichat.models import ControlledDocument, ControlledDocumentState, RagProjectionGate
from aichat.services import applicability
from assets.models import AssetMachine, Client
from repair import services

_SCOPE = 'local-test-site'
_CLIENT_IDS = []


def _scopes(actor):
    return {
        MaintenanceScope(customer_id=None, site_key=None, client_id=pk)
        for pk in _CLIENT_IDS
    }


def _capabilities(actor):
    return {'diagnostics.manuals.read'}


def _write_pdf(path):
    writer = PdfWriter()
    for text in (
        'General information',
        'Bearing inspection requires a qualified technician.',
    ):
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({
            NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/Helvetica'),
        })
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({
                NameObject('/F1'): writer._add_object(font)
            })
        })
        stream = DecodedStreamObject()
        stream.set_data(f'BT /F1 12 Tf 50 700 Td ({text}) Tj ET'.encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    with path.open('wb') as output:
        writer.write(output)


@override_settings(
    USE_TZ=True,
    AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER='repair.test_local_controlled_documents._capabilities',
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='repair.test_local_controlled_documents._scopes',
)
class LocalControlledDocumentTests(TestCase):
    """Exercise native ACLs, applicability, byte identity and citations."""

    def setUp(self):
        """Create an approved, byte-anchored PDF and two machine scopes."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'motor.pdf'
        _write_pdf(self.source)
        self.settings = SimpleNamespace(
            ai_provider='local',
            env='development',
            single_site_policy_key=_SCOPE,
            controlled_documents_root=self.root,
        )
        settings_patch = patch(
            'ai.core.config.get_settings', return_value=self.settings
        )
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        suffix = uuid.uuid4().hex[:8]
        users = get_user_model().objects
        self.actor = users.create_superuser(username=f'reader-{suffix}', password='pw')
        self.proposer = users.create_user(username=f'proposer-{suffix}')
        self.verifier = users.create_superuser(
            username=f'verifier-{suffix}', password='pw'
        )
        tenant = Client.objects.create(name='Local site', code=f'site-{suffix}')
        other_tenant = Client.objects.create(name='Other site', code=f'other-{suffix}')
        _CLIENT_IDS[:] = [tenant.pk]
        self.addCleanup(_CLIENT_IDS.clear)
        self.machine = AssetMachine.objects.create(name='Pump', client=tenant)
        self.other_machine = AssetMachine.objects.create(
            name='Other pump', client=other_tenant
        )
        self.document = ControlledDocument.objects.create(
            document_id='exercise-motor',
            revision='exercise-1',
            title='Motor manual',
            document_class='technical_manual',
            scope_key=_SCOPE,
            scope_hash=hashlib.sha256(_SCOPE.encode()).hexdigest(),
            access_class='maintenance_authorized',
            source_filename=self.source.name,
            source_location=str(self.source),
            source_sha256=hashlib.sha256(self.source.read_bytes()).hexdigest(),
            state=ControlledDocumentState.INDEXED,
            is_current=True,
            search_index_name='local-pdf-lexical',
            approved_by=self.verifier,
        )
        self.claim = applicability.propose(
            document=self.document,
            kind='exact_machine',
            actor=self.proposer,
            basis='Test commissioning record',
            target_machine_id=self.machine.pk,
        )
        applicability.verify(self.claim.pk, actor=self.verifier)

    def _authorization(self):
        decision = services.authorize_diagnostic_read(
            actor=self.actor,
            capability='diagnostics.manuals.read',
            entity_type='machine',
            entity_id=self.machine.pk,
            expected_revision=services._diagnostic_revision(self.machine),
            linked_machine_id=None,
            check_id='test',
        )
        return SimpleNamespace(**decision)

    def _read(self, authorization=None, revision=None):
        return services.read_diagnostic_approved_manuals(
            actor=self.actor,
            authorization=authorization or self._authorization(),
            machine_id=self.machine.pk,
            expected_revision=revision or services._diagnostic_revision(self.machine),
            query='bearing inspection',
            limit=5,
        )

    def test_approved_exact_machine_returns_real_page_and_hash(self):
        """A verified source returns its actual page, bytes and excerpt."""
        evidence = self._read()['evidence']
        self.assertEqual(len(evidence), 1)
        item = evidence[0]
        claim = json.loads(item['claim'])
        self.assertEqual(claim['page'], 2)
        self.assertEqual(claim['source_filename'], self.source.name)
        self.assertEqual(
            claim['source_sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest()
        )
        self.assertEqual(
            claim['excerpt'], 'Bearing inspection requires a qualified technician.'
        )
        self.assertEqual(item['revision'], 'exercise-1')
        self.assertIn('#page=2', item['locator'])
        self.assertTrue(item['untrusted'])

    def test_production_diagnostic_registry_reads_and_fences_the_source(self):
        """The actual diagnostic adapter returns evidence and honest abstention."""
        from ai.core.auth import AIPrincipal
        from ai.core.tools.diagnostics import (
            DiagnosticRecordRoot,
            build_diagnostic_context,
            get_diagnostic_tool_registry,
        )

        actor_id = f'user:{self.actor.pk}'
        principal = AIPrincipal(
            subject=actor_id,
            actor=actor_id,
            user_pk=str(self.actor.pk),
            username=self.actor.username,
            authentication_method='django_session',
            scope=_SCOPE,
            policy_version='test',
            is_staff=True,
            is_superuser=True,
        )
        revision = services._diagnostic_revision(self.machine)
        context = build_diagnostic_context(
            principal,
            server_record_roots=(
                DiagnosticRecordRoot(
                    entity_type='machine',
                    entity_id=self.machine.pk,
                    expected_revision=revision,
                ),
            ),
            server_allowed_capabilities=('diagnostics.manuals.read',),
        )
        registry = get_diagnostic_tool_registry()
        arguments = {
            'machine_id': self.machine.pk,
            'expected_revision': revision,
            'query': 'bearing inspection',
            'limit': 5,
        }
        result = registry.execute('search_approved_manuals', arguments, context=context)
        self.assertEqual(result['status'], 'ok')
        self.assertIn('[UNTRUSTED-CONTENT-BEGIN]', result['evidence'][0]['claim'])
        self.assertIn('Bearing inspection', result['evidence'][0]['claim'])
        self.document.approved_by = None
        self.document.save(update_fields=['approved_by'])
        result = registry.execute('search_approved_manuals', arguments, context=context)
        self.assertEqual(result['status'], 'abstain')
        self.assertEqual(result['evidence'], [])

    def test_unapproved_and_unverified_documents_abstain(self):
        """Approval and live applicability are independent requirements."""
        self.document.approved_by = None
        self.document.save(update_fields=['approved_by'])
        self.assertFalse(self._read()['evidence'])
        self.document.approved_by = self.verifier
        self.document.save(update_fields=['approved_by'])
        applicability.revoke(
            self.claim.pk, actor=self.verifier, reason='Test revocation'
        )
        self.assertFalse(self._read()['evidence'])

    def test_foreign_scope_wrong_access_and_superseded_abstain(self):
        """Only current records in the configured authorized site are read."""
        for field, value in (
            ('scope_key', 'foreign'),
            ('scope_hash', '0' * 64),
            ('access_class', 'internal'),
        ):
            original = getattr(self.document, field)
            setattr(self.document, field, value)
            self.document.save(update_fields=[field])
            self.assertFalse(self._read()['evidence'], field)
            setattr(self.document, field, original)
            self.document.save(update_fields=[field])
        self.document.is_current = False
        self.document.state = ControlledDocumentState.SUPERSEDED
        self.document.save(update_fields=['is_current', 'state'])
        self.assertFalse(self._read()['evidence'])

    def test_wrong_machine_and_hash_mismatch_abstain(self):
        """Machine identity and source bytes must match the live claim."""
        self.claim.target_machine_id = self.other_machine.pk
        self.claim.save(update_fields=['target_machine_id'])
        self.assertFalse(self._read()['evidence'])
        self.claim.target_machine_id = self.machine.pk
        self.claim.save(update_fields=['target_machine_id'])
        self.source.write_bytes(self.source.read_bytes() + b'changed')
        self.assertFalse(self._read()['evidence'])

    def test_claim_fingerprint_and_expiration_are_enforced(self):
        """A stale fingerprint or expired verification grants no passage."""
        from datetime import timedelta

        from django.utils import timezone

        self.claim.document_content_sha256 = '0' * 64
        self.claim.save(update_fields=['document_content_sha256'])
        self.assertFalse(self._read()['evidence'])
        self.claim.document_content_sha256 = self.document.source_sha256
        self.claim.effective_to = timezone.now().date() - timedelta(days=1)
        self.claim.save(update_fields=['document_content_sha256', 'effective_to'])
        self.assertFalse(self._read()['evidence'])

    def test_unsafe_uri_and_escaped_symlink_abstain(self):
        """URI and symlink tricks cannot escape the controlled root."""
        self.document.source_location = 'file://' + str(self.source)
        self.document.save(update_fields=['source_location'])
        self.assertFalse(self._read()['evidence'])
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / self.source.name
            external.write_bytes(self.source.read_bytes())
            link = self.root / 'escape'
            link.symlink_to(outside, target_is_directory=True)
            self.document.source_location = str(link / self.source.name)
            self.document.save(update_fields=['source_location'])
            self.assertFalse(self._read()['evidence'])

    def test_actor_scope_revision_capability_and_projection_gate_are_rechecked(self):
        """Recheck actor, machine revision, capability and projection gate."""
        authorization = self._authorization()
        self.assertFalse(self._read(authorization, revision='stale')['evidence'])
        authorization.capability = 'diagnostics.machine.read'
        self.assertFalse(self._read(authorization)['evidence'])
        _CLIENT_IDS.clear()
        self.assertFalse(
            self._read(self._authorization_for_existing_machine())['evidence']
        )
        _CLIENT_IDS.append(self.machine.client_id)
        RagProjectionGate.objects.create(corpus='controlled', blocked=True)
        self.assertFalse(self._read()['evidence'])

    def _authorization_for_existing_machine(self):
        return SimpleNamespace(
            capability='diagnostics.manuals.read',
            entity_type='machine',
            entity_id=self.machine.pk,
            linked_machine_id=None,
            check_id='test',
            actor_id=f'user:{self.actor.pk}',
            current_revision=services._diagnostic_revision(self.machine),
        )

    def test_local_only_and_corpus_share_the_real_reader(self):
        """The local corpus never uses an embedding client or widens scope."""
        with patch(
            'ai.core.integrations.controlled_document_corpus._default_embedding_client'
        ) as cloud:
            result = search_corpus(
                user=self.actor,
                query='bearing inspection',
                machine='Pump',
                machine_resolver=lambda actor, name: [{'machine_id': self.machine.pk}],
            )
        cloud.assert_not_called()
        self.assertEqual(result['returned_count'], 1)
        self.assertEqual(
            result['chunks'][0]['citation']['source_sha256'],
            self.document.source_sha256,
        )
        self.assertIn('Bearing inspection', result['chunks'][0]['excerpt'])
        self.assertFalse(search_corpus(user=self.actor, query='bearing')['chunks'])
        self.settings.ai_provider = 'azure'
        self.assertFalse(self._read()['evidence'])
        self.settings.ai_provider = 'local'
        self.settings.env = 'production'
        self.assertFalse(
            search_local_manuals(
                user=self.actor, machine_id=self.machine.pk, query='bearing'
            )
        )

    def test_deactivated_actor_or_revoked_work_role_abstains(self):
        """A prior authorization cannot survive an actor or role change."""
        authorization = self._authorization()
        self.actor.is_active = False
        self.actor.save(update_fields=['is_active'])
        self.assertFalse(self._read(authorization)['evidence'])
        self.actor.is_active = True
        self.actor.is_superuser = False
        self.actor.save(update_fields=['is_active', 'is_superuser'])
        self.assertFalse(self._read(authorization)['evidence'])

    def test_final_machine_revision_is_checked_after_pdf_read(self):
        """A machine changed during extraction cannot yield stale evidence."""
        authorization = self._authorization()
        revision = services._diagnostic_revision(self.machine)
        from ai.core.integrations import local_controlled_documents

        original = local_controlled_documents._page_hits

        def change_revision(*args):
            result = original(*args)
            self.machine.name = 'Changed'
            self.machine.save()
            return result

        with patch.object(
            local_controlled_documents, '_page_hits', side_effect=change_revision
        ):
            self.assertFalse(self._read(authorization, revision=revision)['evidence'])

    def test_a_stale_grant_cannot_be_reused_for_a_new_revision(self):
        """The original authorization must pin the revision being read."""
        authorization = self._authorization()
        self.machine.name = 'Updated machine'
        self.machine.save()
        self.assertFalse(
            self._read(
                authorization, revision=services._diagnostic_revision(self.machine)
            )['evidence']
        )

    def test_document_metadata_change_during_read_abstains(self):
        """A citation cannot use a document revision changed during extraction."""
        from ai.core.integrations import local_controlled_documents

        original = local_controlled_documents._page_hits

        def change_document(*args):
            result = original(*args)
            ControlledDocument.objects.filter(pk=self.document.pk).update(
                revision='changed'
            )
            return result

        with patch.object(
            local_controlled_documents, '_page_hits', side_effect=change_document
        ):
            self.assertFalse(self._read()['evidence'])

    def test_azure_provider_keeps_existing_search_path(self):
        """Only an explicitly local provider selects lexical PDF retrieval."""
        self.settings.ai_provider = 'azure'
        search_client = SimpleNamespace(search=lambda **kwargs: [])
        embedding_client = SimpleNamespace(embed_batch=lambda inputs: [[0.5] * 3072])
        with patch.object(
            search_client, 'search', wraps=search_client.search
        ) as search:
            result = search_corpus(
                user=self.actor,
                query='bearing inspection',
                search_client=search_client,
                embedding_client=embedding_client,
            )
        self.assertEqual(search.call_count, 1)
        self.assertEqual(result['returned_count'], 0)
