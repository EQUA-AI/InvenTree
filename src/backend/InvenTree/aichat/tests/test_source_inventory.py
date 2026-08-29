"""S8a WP-B1: registry-backed source inventory (the gateway core)."""

import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from tasks.scope import MaintenanceScope

from aichat.models import (
    AttachmentIngest,
    AttachmentIngestPipeline,
    AttachmentIngestState,
    ControlledDocument,
    ControlledDocumentState,
)

_MEDIA_ROOT = tempfile.mkdtemp(prefix='aimms-source-inventory-')

SCOPE_KEY = 'epcon-experimental'

# The tools reload the acting user by pk, so grants are served the way
# production serves them: through the configured resolver seam.
_GRANTS: dict[str, set[MaintenanceScope]] = {}


def _grant_resolver(actor):
    """Resolver seam: return whatever the test granted this username."""
    return _GRANTS.get(actor.get_username(), set())


def _document(**overrides) -> ControlledDocument:
    values = {
        'document_id': 'aimms-hx200-manual',
        'revision': '2.0',
        'title': 'HX-200 Technical Manual',
        'document_class': 'technical_manual',
        'scope_key': SCOPE_KEY,
        'scope_hash': 'a' * 64,
        'access_class': 'maintenance_authorized',
        'source_filename': 'hx200-manual.md',
        'source_location': '/tmp/hx200-manual.md',
        'source_sha256': 'b' * 64,
        'asset_id': 'EVAL-HX200',
        'state': ControlledDocumentState.INDEXED,
        'is_current': True,
        'search_index_name': 'eaits-manuals-v4a',
    }
    values.update(overrides)
    return ControlledDocument.objects.create(**values)


@override_settings(
    MEDIA_ROOT=_MEDIA_ROOT,
    AIMMS_SINGLE_SITE_CLIENT_CODE='acme',
    AIMMS_MACHINE_AI_READ_ENABLED=True,
    AIMMS_MAINTENANCE_AI_READ_ENABLED=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER=f'{__name__}._grant_resolver',
)
class SourceInventoryTests(TestCase):
    """Inventory answers come from registries, honestly, with A11 states."""

    @classmethod
    def setUpTestData(cls):
        """One authorized machine world under the 'acme' site client."""
        from assets.models import AssetMachine, Client

        cls.acme = Client.objects.create(name='Acme Solar', code='acme')
        cls.machine = AssetMachine.objects.create(
            name='HX-200 Heat Exchanger', client=cls.acme, serial='EVAL-HX200'
        )
        cls.serial_less = AssetMachine.objects.create(
            name='Unstamped Pump', client=cls.acme, serial=''
        )
        cls.user = get_user_model().objects.create_superuser(
            username='inventory-user', password='x'
        )
        _GRANTS['inventory-user'] = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=cls.acme.pk)
        }

    # -- controlled documents ------------------------------------------

    def test_document_lifecycle_matrix_reports_honest_states(self):
        """Current / superseded / failed / draft rows each state honestly."""
        from ai.core.analysis.source_gateway import controlled_document_inventory

        _document()  # current + indexed
        _document(
            revision='1.0',
            source_sha256='c' * 64,
            is_current=False,
            state=ControlledDocumentState.SUPERSEDED,
        )
        _document(
            document_id='aimms-hx200-datasheet',
            title='HX-200 Datasheet',
            revision='A',
            source_sha256='d' * 64,
            is_current=False,
            state=ControlledDocumentState.FAILED,
            search_index_name='',
            indexing_error_code='EXTRACTION_FAILED',
        )
        _document(
            document_id='aimms-site-safety-plan',
            title='Site Safety Plan',
            revision='4',
            source_sha256='e' * 64,
            asset_id='',
            is_current=True,
        )

        section = controlled_document_inventory(scope_key=SCOPE_KEY)
        self.assertEqual(section['population_count'], 3)
        by_id = {entry['document_id']: entry for entry in section['documents']}

        manual = by_id['aimms-hx200-manual']
        self.assertEqual(manual['current']['revision'], '2.0')
        self.assertIn(manual['current']['approved'], (True, False))
        self.assertEqual(manual['superseded_revision_count'], 1)
        self.assertEqual(manual['association'], 'ingest_asset_serial')
        self.assertEqual(
            manual['source_state'],
            {
                'registered': True,
                'attached': True,
                'indexed': True,
                'applicable': False,
                'searchable_now': True,
                'current': True,
            },
        )

        datasheet = by_id['aimms-hx200-datasheet']
        self.assertIsNone(datasheet['current'])
        self.assertEqual(
            datasheet['pending_or_failed'][0]['error_code'], 'EXTRACTION_FAILED'
        )
        self.assertFalse(datasheet['source_state']['searchable_now'])

        site_wide = by_id['aimms-site-safety-plan']
        self.assertEqual(site_wide['association'], 'site_wide')
        self.assertFalse(site_wide['source_state']['attached'])

        for entry in section['documents']:
            self.assertFalse(entry['source_state']['applicable'])
            self.assertEqual(entry['applicability'], 'unresolved')

    def test_foreign_scope_key_rows_never_appear(self):
        """The deployment boundary is always part of the registry query."""
        from ai.core.analysis.source_gateway import controlled_document_inventory

        _document(scope_key='other-boundary', scope_hash='f' * 64)
        section = controlled_document_inventory(scope_key=SCOPE_KEY)
        self.assertEqual(section['population_count'], 0)
        self.assertEqual(section['documents'], [])

    def test_blank_scope_key_refuses(self):
        """An unconfigured boundary never widens into 'all documents'."""
        from ai.core.analysis.source_gateway import controlled_document_inventory

        _document()
        section = controlled_document_inventory(scope_key='')
        self.assertTrue(section['unavailable'])
        self.assertEqual(section['code'], 'scope_unconfigured')

    def test_serial_narrowing_keeps_labeled_site_wide_rows(self):
        """Asset narrowing includes blank-stamp docs, labeled (§8.4 step 4)."""
        from ai.core.analysis.source_gateway import (
            controlled_document_inventory,
            resolve_asset_set,
        )

        _document()
        _document(
            document_id='aimms-site-safety-plan',
            title='Site Safety Plan',
            revision='4',
            source_sha256='e' * 64,
            asset_id='',
        )
        _document(
            document_id='aimms-other-machine-manual',
            title='Other Machine Manual',
            revision='1',
            source_sha256='f' * 64,
            asset_id='OTHER-SERIAL',
        )
        asset_set = resolve_asset_set(self.user, [self.machine.pk])
        section = controlled_document_inventory(
            scope_key=SCOPE_KEY, asset_set=asset_set
        )
        ids = {entry['document_id'] for entry in section['documents']}
        self.assertEqual(ids, {'aimms-hx200-manual', 'aimms-site-safety-plan'})
        self.assertEqual(section['population_count'], 2)

    def test_empty_registry_is_an_honest_zero(self):
        """Zero rows -> population 0, complete, no invented absence claims."""
        from ai.core.analysis.source_gateway import inventory

        ai_settings = mock.Mock()
        ai_settings.single_site_policy_key = SCOPE_KEY
        ai_settings.azure_search_controlled_documents_index = ''
        with mock.patch('ai.core.config.get_settings', return_value=ai_settings):
            result = inventory(self.user, source_classes=['controlled_document'])
        section = result['sections']['controlled_documents']
        self.assertEqual(section['population_count'], 0)
        self.assertEqual(section['documents'], [])
        self.assertTrue(section['retrieval']['coverage']['complete_population'])

    # -- asset set -------------------------------------------------------

    def test_resolve_asset_set_authorizes_and_tracks_serials(self):
        """Unknown ids drop silently; serial-less machines are flagged."""
        from ai.core.analysis.source_gateway import resolve_asset_set

        asset_set = resolve_asset_set(
            self.user, [self.machine.pk, self.serial_less.pk, 999999]
        )
        self.assertEqual(
            [name for _, name, _ in asset_set.machines],
            ['HX-200 Heat Exchanger', 'Unstamped Pump'],
        )
        self.assertEqual(asset_set.serials, frozenset({'EVAL-HX200'}))
        self.assertEqual(asset_set.serial_less, ('Unstamped Pump',))
        self.assertIn('serial_unresolved', asset_set.warnings)

    def test_resolve_asset_set_intersects_with_enforced_scope(self):
        """Requested ids only ever NARROW an explicit enforced scope."""
        from ai.core.analysis.scope_context import TurnScopeContext, turn_scope_context
        from ai.core.analysis.source_gateway import resolve_asset_set

        scope = TurnScopeContext(
            mode='explicit_assets',
            machine_ids=(self.machine.pk,),
            machine_serials=frozenset({'EVAL-HX200'}),
            date_from=None,
            date_to=None,
            source_classes=(),
            scope_hash='hash',
            scope_version=1,
            snapshot_id='snap_x',
            thread_pk='thread_x',
            display_label='HX-200',
            shadow=True,
            enforce=True,
        )
        token = turn_scope_context.set(scope)
        try:
            narrowed = resolve_asset_set(
                self.user, [self.machine.pk, self.serial_less.pk]
            )
        finally:
            turn_scope_context.reset(token)
        self.assertEqual(narrowed.machine_pks, (self.machine.pk,))
        self.assertTrue(
            any(w.startswith('narrowed_to_analysis_scope') for w in narrowed.warnings)
        )

    # -- attachments -----------------------------------------------------

    def _attachment(self, model_type, model_id, name='note.pdf'):
        from common.models import Attachment

        with mock.patch('InvenTree.tasks.offload_task', return_value=True):
            return Attachment.objects.create(
                model_type=model_type,
                model_id=model_id,
                attachment=SimpleUploadedFile(name, b'content'),
                comment='uploaded doc',
            )

    def _ingest(self, attachment, **overrides):
        values = {
            'attachment_id': attachment.pk,
            'model_type': 'assetmachine',
            'model_id': self.machine.pk,
            'client_codes': ['acme'],
            'source_sha256': '0' * 64,
            'pipeline': AttachmentIngestPipeline.DOC,
            'state': AttachmentIngestState.INDEXED,
            'search_index_name': 'attachment-index',
        }
        values.update(overrides)
        return AttachmentIngest.objects.create(**values)

    def test_attachment_join_reports_unregistered_failed_and_withheld(self):
        """The honesty cases the plain attachments tool cannot express."""
        from ai.core.analysis.source_gateway import (
            attachment_inventory,
            resolve_asset_set,
        )

        self._attachment('assetmachine', self.machine.pk, 'raw.pdf')
        indexed = self._attachment('assetmachine', self.machine.pk, 'indexed.pdf')
        self._ingest(indexed)
        failed = self._attachment('assetmachine', self.machine.pk, 'failed.pdf')
        self._ingest(failed, state=AttachmentIngestState.FAILED, error_code='STALLED')
        foreign = self._attachment('assetmachine', self.machine.pk, 'foreign.pdf')
        self._ingest(foreign, client_codes=['someone-else'])
        clientless = self._attachment('assetmachine', self.machine.pk, 'orphan.pdf')
        self._ingest(clientless, client_codes=[])

        asset_set = resolve_asset_set(self.user, [self.machine.pk])
        section = attachment_inventory(user=self.user, asset_set=asset_set)

        self.assertEqual(section['population_count'], 5)
        self.assertEqual(section['withheld_count'], 1)
        by_state = {}
        for item in section['attachments']:
            by_state.setdefault(item['ingest_state'], []).append(item)

        self.assertEqual(
            by_state['unregistered'][0]['source_state']['registered'], False
        )
        searchable_flags = sorted(
            item['source_state']['searchable_now'] for item in by_state['indexed']
        )
        # Exactly one of the two indexed rows is searchable: the 'acme'
        # stamped one; the clientless (fail-closed) row never is.
        self.assertEqual(searchable_flags, [False, True])
        self.assertEqual(by_state['failed'][0]['error_code'], 'STALLED')
        for item in section['attachments']:
            self.assertFalse(item['source_state']['applicable'])
            self.assertEqual(item['control_class'], 'uncontrolled_attachment')

        # The clientless row is listed but can never be searchable.
        clientless_items = [
            item
            for item in by_state['indexed']
            if not item['source_state']['searchable_now']
        ]
        self.assertEqual(len(clientless_items), 1)

    def test_attachment_inventory_requires_an_asset_selection(self):
        """Site-wide attachment listing is refused, not silently global."""
        from ai.core.analysis.source_gateway import AssetSet, attachment_inventory

        empty = AssetSet(machines=(), serials=frozenset(), serial_less=(), warnings=())
        section = attachment_inventory(user=self.user, asset_set=empty)
        self.assertEqual(section['population_count'], 0)
        self.assertIn('asset_selection_required', section['warnings'])

    # -- top level -------------------------------------------------------

    def test_inventory_sections_carry_envelopes_and_warnings(self):
        """Each section gets its own §7.4 envelope; applicability warned."""
        from ai.core.analysis.source_gateway import inventory

        _document()
        ai_settings = mock.Mock()
        ai_settings.single_site_policy_key = SCOPE_KEY
        ai_settings.azure_search_controlled_documents_index = 'eaits-manuals-v4a'
        with mock.patch(
            'ai.core.config.get_settings', return_value=ai_settings
        ):
            result = inventory(self.user, machine_ids=[self.machine.pk])

        self.assertIn(
            'applicability_unresolved', result['warnings']
        )
        controlled = result['sections']['controlled_documents']
        self.assertEqual(controlled['population_count'], 1)
        envelope = controlled['retrieval']
        self.assertEqual(envelope['population_type'], 'registry')
        self.assertTrue(envelope['coverage']['complete_population'])
        self.assertEqual(envelope['operation'], 'source_inventory')
        self.assertEqual(
            result['sections']['thread_uploads']['available'], 'in_conversation_only'
        )

    def test_unknown_source_class_is_refused(self):
        """The class filter is an allowlist, never free text."""
        from ai.core.analysis.source_gateway import inventory

        result = inventory(self.user, source_classes=['secrets'])
        self.assertTrue(result['unavailable'])
        self.assertEqual(result['code'], 'unknown_source_class')


@override_settings(
    MEDIA_ROOT=_MEDIA_ROOT,
    AIMMS_SINGLE_SITE_CLIENT_CODE='acme',
)
class DocumentResolutionTests(TestCase):
    """S8a WP-B3: name -> current registry row; ambiguity asks, never guesses."""

    def test_exact_id_then_exact_title_then_unique_substring(self):
        """The resolution ladder, in order, current rows only."""
        from ai.core.analysis.source_gateway import (
            AmbiguousDocumentRef,
            resolve_selected_document,
        )

        manual = _document()
        _document(
            document_id='aimms-hx200-datasheet',
            title='HX-200 Datasheet',
            revision='A',
            source_sha256='d' * 64,
        )

        by_id = resolve_selected_document(
            scope_key=SCOPE_KEY, document_ref='aimms-hx200-manual'
        )
        self.assertEqual(by_id.pk, manual.pk)

        by_title = resolve_selected_document(
            scope_key=SCOPE_KEY, document_ref='hx-200 technical manual'
        )
        self.assertEqual(by_title.pk, manual.pk)

        by_substring = resolve_selected_document(
            scope_key=SCOPE_KEY, document_ref='Technical Manual'
        )
        self.assertEqual(by_substring.pk, manual.pk)

        ambiguous = resolve_selected_document(
            scope_key=SCOPE_KEY, document_ref='HX-200'
        )
        self.assertIsInstance(ambiguous, AmbiguousDocumentRef)
        self.assertEqual(len(ambiguous.candidates), 2)

        self.assertIsNone(
            resolve_selected_document(scope_key=SCOPE_KEY, document_ref='Nothing Here')
        )
        # Blank scope key resolves nothing, ever.
        self.assertIsNone(
            resolve_selected_document(scope_key='', document_ref='aimms-hx200-manual')
        )

    def test_superseded_revisions_never_resolve(self):
        """Only is_current rows are selectable for pinning."""
        from ai.core.analysis.source_gateway import resolve_selected_document

        _document(
            is_current=False,
            state=ControlledDocumentState.SUPERSEDED,
        )
        self.assertIsNone(
            resolve_selected_document(
                scope_key=SCOPE_KEY, document_ref='aimms-hx200-manual'
            )
        )

    def test_display_title_is_scope_key_bound(self):
        """The cross-boundary label leak (§8.4 line 793) is closed."""
        from ai.core.integrations.controlled_document_corpus import _display_title

        _document(title='Our Boundary Title')
        _document(
            scope_key='other-boundary',
            scope_hash='f' * 64,
            title='Foreign Boundary Title',
        )
        row = {'document_id': 'aimms-hx200-manual', 'document_revision': '2.0'}
        self.assertEqual(
            _display_title(row, scope_key=SCOPE_KEY), 'Our Boundary Title'
        )
        # The foreign boundary's registry may NEVER supply this label; with
        # no local row the fallback derives from the file name instead.
        self.assertEqual(
            _display_title(row, scope_key='unregistered-boundary'),
            'aimms-hx200-manual (rev 2.0)',
        )
