"""S8b WP-C9: the `applicable` flip — verified rows resolve real verdicts.

The three pre-S8b pins in ``test_source_inventory`` keep asserting the
no-claim state (unresolved, never asserted); this suite covers the other
half: a verified row flips exactly its own site, and nothing else.
"""

import tempfile
import unittest
import uuid
from unittest import mock

from django.apps import apps

if not apps.is_installed('assets'):
    raise unittest.SkipTest('requires the full InvenTree app registry')

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings

from assets.models import AssetMachine, Client
from aichat.models import ControlledDocument, ControlledDocumentState
from aichat.reports import pilot_metrics
from aichat.services import applicability

SCOPE_KEY = 'epcon-experimental'
_MEDIA_ROOT = tempfile.mkdtemp(prefix='aimms-applicability-flip-')

_GRANTS: dict[int, set] = {}


def _grant_resolver(actor):
    return _GRANTS.get(getattr(actor, 'pk', None), set())


@override_settings(
    MEDIA_ROOT=_MEDIA_ROOT,
    AIMMS_SINGLE_SITE_CLIENT_CODE='acme',
    AIMMS_MACHINE_AI_READ_ENABLED=True,
    AIMMS_MAINTENANCE_AI_READ_ENABLED=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER=f'{__name__}._grant_resolver',
)
class ApplicabilityFlipTestCase(TestCase):
    """One verified and one unverified document over one small fleet."""

    @classmethod
    def setUpTestData(cls):
        from tasks.scope import MaintenanceScope

        suffix = uuid.uuid4().hex[:6]
        cls.tenant = Client.objects.create(name=f'Acme Solar {suffix}', code='acme')
        cls.machine = AssetMachine.objects.create(
            name=f'HX-200 Heat Exchanger {suffix}',
            client=cls.tenant,
            serial='EVAL-HX200',
            model='SINVERT PVS351',
        )
        cls.serial_less_machine = AssetMachine.objects.create(
            name=f'Unstamped Pump {suffix}',
            client=cls.tenant,
            serial='',
            model='',
        )
        users = get_user_model().objects
        cls.user = users.create_superuser(
            username=f'flip-user-{suffix}', email='fu@example.com', password='pw'
        )
        cls.proposer = users.create_user(username=f'flip-proposer-{suffix}')
        cls.verifier = users.create_user(username=f'flip-verifier-{suffix}')
        cls.engineer = users.create_user(username=f'flip-engineer-{suffix}')
        cls.verifier.user_permissions.add(
            Permission.objects.get(
                codename='verify_document_applicability',
                content_type__app_label='aichat',
            )
        )
        cls.engineer.user_permissions.add(
            Permission.objects.get(
                codename='countersign_document_applicability',
                content_type__app_label='aichat',
            )
        )
        _GRANTS[cls.user.pk] = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=cls.tenant.pk)
        }
        cls.verified_document = cls._document(
            f'flip-manual-{suffix}', asset_id='EVAL-HX200'
        )
        cls.unverified_document = cls._document(
            f'flip-datasheet-{suffix}', asset_id='EVAL-HX200'
        )

    @classmethod
    def _document(cls, document_id: str, **overrides) -> ControlledDocument:
        values = {
            'document_id': document_id,
            'revision': '2.0',
            'title': f'{document_id} title',
            'document_class': 'technical_manual',
            'scope_key': SCOPE_KEY,
            'scope_hash': 'a' * 64,
            'access_class': 'maintenance_authorized',
            'source_filename': f'{document_id}.md',
            'source_location': f'/tmp/{document_id}.md',
            'source_sha256': 'b' * 64,
            'asset_id': '',
            'state': ControlledDocumentState.INDEXED,
            'is_current': True,
            'search_index_name': 'eaits-manuals-v4a',
        }
        values.update(overrides)
        return ControlledDocument.objects.create(**values)

    def _verify_exact(self, document, machine):
        row = applicability.propose(
            document=document,
            kind='exact_machine',
            actor=self.proposer,
            basis='commissioning record',
            target_machine_id=machine.pk,
            target_serial=machine.serial,
        )
        return applicability.verify(row.pk, actor=self.verifier)


class InventoryFlipTests(ApplicabilityFlipTestCase):
    """A verified row flips exactly its own inventory entry."""

    def test_verified_row_flips_its_entry_only(self):
        from ai.core.analysis.source_gateway import (
            controlled_document_inventory,
            resolve_asset_set,
        )

        self._verify_exact(self.verified_document, self.machine)
        asset_set = resolve_asset_set(self.user, [self.machine.pk])
        section = controlled_document_inventory(
            scope_key=SCOPE_KEY, asset_set=asset_set
        )
        by_id = {entry['document_id']: entry for entry in section['documents']}
        verified = by_id[self.verified_document.document_id]
        self.assertEqual(verified['applicability'], 'verified')
        self.assertTrue(verified['source_state']['applicable'])
        unverified = by_id[self.unverified_document.document_id]
        self.assertEqual(unverified['applicability'], 'unresolved')
        self.assertFalse(unverified['source_state']['applicable'])
        self.assertEqual(section['unresolved_applicability_count'], 1)

    def test_warning_disappears_when_everything_is_verified(self):
        from ai.core.analysis.source_gateway import inventory

        self._verify_exact(self.verified_document, self.machine)
        self._verify_exact(self.unverified_document, self.machine)
        ai_settings = mock.Mock()
        ai_settings.single_site_policy_key = SCOPE_KEY
        ai_settings.azure_search_controlled_documents_index = 'idx'
        with mock.patch('ai.core.config.get_settings', return_value=ai_settings):
            result = inventory(
                self.user,
                machine_ids=[self.machine.pk],
                source_classes=['controlled_document'],
            )
        self.assertNotIn('applicability_unresolved', result['warnings'])
        controlled = result['sections']['controlled_documents']
        self.assertEqual(controlled['unresolved_applicability_count'], 0)
        self.assertEqual(controlled['retrieval']['warnings'], [])


class ManualFactRerouteTests(ApplicabilityFlipTestCase):
    """The preference order gains the two verified S8b steps."""

    def test_serial_less_machine_reaches_its_verified_document(self):
        from ai.core.analysis.source_gateway import retrieve_manual_fact

        self._verify_exact(self.verified_document, self.serial_less_machine)
        pinned_calls: list[dict] = []

        def pinned_search(**kwargs):
            pinned_calls.append(kwargs)
            return {'chunks': [{'excerpt': 'fenced', 'citation': {}}]}

        result = retrieve_manual_fact(
            self.user,
            query='torque spec',
            machine_ids=[self.serial_less_machine.pk],
            corpus_search=mock.Mock(),
            pinned_search=pinned_search,
        )
        self.assertEqual(result['labels'], ['verified_exact_applicability'])
        self.assertEqual(
            pinned_calls[0]['document'].pk, self.verified_document.pk
        )
        steps = [attempt['step'] for attempt in result['attempts']]
        self.assertEqual(steps, ['verified_exact_controlled'])

    def test_serial_less_machine_without_claims_stays_unresolved(self):
        from ai.core.analysis.source_gateway import retrieve_manual_fact

        corpus = mock.Mock()
        result = retrieve_manual_fact(
            self.user,
            query='torque spec',
            machine_ids=[self.serial_less_machine.pk],
            corpus_search=corpus,
            pinned_search=mock.Mock(),
        )
        corpus.assert_not_called()
        self.assertEqual(result['applicability'], 'unresolved')
        self.assertTrue(result['scope_miss'])

    def test_verified_model_step_runs_between_exact_and_fleet(self):
        from ai.core.analysis.source_gateway import retrieve_manual_fact

        row = applicability.propose(
            document=self.verified_document,
            kind='inverter_model',
            actor=self.proposer,
            basis='nameplate survey',
            target_model='SINVERT PVS351',
        )
        applicability.verify(row.pk, actor=self.verifier)
        applicability.countersign(row.pk, actor=self.engineer)

        def corpus_search(**kwargs):
            return {'chunks': []}

        pinned_calls: list[dict] = []

        def pinned_search(**kwargs):
            pinned_calls.append(kwargs)
            return {'chunks': [{'excerpt': 'fenced', 'citation': {}}]}

        result = retrieve_manual_fact(
            self.user,
            query='dc link fault',
            machine_ids=[self.machine.pk],
            corpus_search=corpus_search,
            pinned_search=pinned_search,
        )
        self.assertEqual(result['labels'], ['verified_model_configuration'])
        steps = [attempt['step'] for attempt in result['attempts']]
        self.assertEqual(steps, ['exact_asset_controlled', 'verified_model_config'])
        self.assertEqual(pinned_calls[0]['document'].pk, self.verified_document.pk)


class CorpusEnvelopeTests(ApplicabilityFlipTestCase):
    """The envelope's `applicable` is a conservative all-of over hits."""

    def _hit(self, document):
        return {
            'document_id': document.document_id,
            'document_revision': document.revision,
        }

    def test_all_hits_verified_flips_the_envelope(self):
        from ai.core.integrations.controlled_document_corpus import (
            _hits_verified_applicable,
        )

        self._verify_exact(self.verified_document, self.machine)
        self.assertTrue(
            _hits_verified_applicable(
                [self._hit(self.verified_document)], serials=('EVAL-HX200',)
            )
        )
        self.assertFalse(
            _hits_verified_applicable(
                [
                    self._hit(self.verified_document),
                    self._hit(self.unverified_document),
                ],
                serials=('EVAL-HX200',),
            )
        )
        self.assertFalse(_hits_verified_applicable([], serials=('EVAL-HX200',)))


class MetricsTests(ApplicabilityFlipTestCase):
    """The pilot metric names the verified relation, not just the proxy."""

    def test_verified_rows_by_kind(self):
        self._verify_exact(self.verified_document, self.machine)
        applicability.propose(
            document=self.unverified_document,
            kind='exact_machine',
            actor=self.proposer,
            basis='pending review',
            target_machine_id=self.machine.pk,
            target_serial=self.machine.serial,
        )
        stats = pilot_metrics.applicability_stats()
        self.assertEqual(stats['verified_rows_by_kind'], {'exact_machine': 1})
        self.assertEqual(stats['proposed_rows'], 1)
