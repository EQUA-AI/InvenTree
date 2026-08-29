"""S8b WP-C7: the applicability model's teeth and the verification workflow."""

import datetime
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import TestCase

from aichat.models import (
    ApplicabilityState,
    ControlledDocument,
    ControlledDocumentApplicability,
    ControlledDocumentState,
)
from aichat.services import applicability

SCOPE_KEY = 'epcon-experimental'


def _permission(codename: str) -> Permission:
    return Permission.objects.get(
        codename=codename, content_type__app_label='aichat'
    )


class ApplicabilityTestCase(TestCase):
    """One indexed document, four humans with distinct authority."""

    @classmethod
    def setUpTestData(cls):
        suffix = uuid.uuid4().hex[:6]
        users = get_user_model().objects
        cls.proposer = users.create_user(username=f'appl-proposer-{suffix}')
        cls.verifier = users.create_user(username=f'appl-verifier-{suffix}')
        cls.engineer = users.create_user(username=f'appl-engineer-{suffix}')
        cls.outsider = users.create_user(username=f'appl-outsider-{suffix}')
        cls.approver = users.create_user(username=f'appl-approver-{suffix}')
        cls.verifier.user_permissions.add(
            _permission('verify_document_applicability')
        )
        cls.engineer.user_permissions.add(
            _permission('countersign_document_applicability')
        )
        cls.document = cls._document(suffix)

    @classmethod
    def _document(cls, suffix: str, **overrides) -> ControlledDocument:
        values = {
            'document_id': f'appl-manual-{suffix}',
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
            'approved_by': cls.approver,
        }
        values.update(overrides)
        return ControlledDocument.objects.create(**values)

    def _proposed(self, **overrides):
        fields = {
            'document': self.document,
            'kind': 'exact_machine',
            'actor': self.proposer,
            'basis': 'commissioning record names this unit',
            'target_machine_id': 12,
            'target_serial': 'EVAL-HX200',
        }
        fields.update(overrides)
        return applicability.propose(**fields)


class ConstraintTests(ApplicabilityTestCase):
    """The database itself enforces the workflow's invariants."""

    def test_proposer_can_never_be_the_verifier(self):
        row = self._proposed()
        row.verified_by = self.proposer
        with self.assertRaises(IntegrityError), transaction.atomic():
            row.save(update_fields=['verified_by'])

    def test_verified_state_requires_the_verification_record(self):
        row = self._proposed()
        row.state = ApplicabilityState.VERIFIED
        with self.assertRaises(IntegrityError), transaction.atomic():
            row.save(update_fields=['state'])

    def test_verified_model_kind_requires_the_countersign(self):
        row = self._proposed(
            kind='inverter_model', target_machine_id=0, target_serial='',
            target_model='SINVERT PVS351',
        )
        row.verified_by = self.verifier
        row.verified_at = datetime.datetime(2026, 8, 29, tzinfo=None)
        row.state = ApplicabilityState.VERIFIED
        with self.assertRaises(IntegrityError), transaction.atomic():
            row.save(update_fields=['verified_by', 'verified_at', 'state'])

    def test_target_shape_matches_the_kind(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ControlledDocumentApplicability.objects.create(
                document=self.document,
                document_content_sha256='b' * 64,
                kind='exact_machine',
                target_machine_id=0,
                proposed_by=self.proposer,
                proposal_basis='x',
            )

    def test_one_live_claim_per_target(self):
        first = self._proposed()
        applicability.verify(first.pk, actor=self.verifier)
        second = self._proposed()
        second.verified_by = self.verifier
        second.verified_at = datetime.datetime(2026, 8, 29, 12, 0)
        second.state = ApplicabilityState.VERIFIED
        with self.assertRaises(IntegrityError), transaction.atomic():
            second.save(update_fields=['verified_by', 'verified_at', 'state'])


class WorkflowTests(ApplicabilityTestCase):
    """Nothing automated verifies; humans with distinct authority do."""

    def test_exact_machine_activates_on_verify(self):
        row = self._proposed()
        self.assertEqual(row.state, ApplicabilityState.PROPOSED)
        row = applicability.verify(row.pk, actor=self.verifier)
        self.assertEqual(row.state, ApplicabilityState.VERIFIED)
        self.assertEqual(row.verified_by_id, self.verifier.pk)

    def test_model_kind_waits_for_the_countersign(self):
        row = self._proposed(
            kind='inverter_model', target_machine_id=0, target_serial='',
            target_model='SINVERT PVS351',
        )
        row = applicability.verify(row.pk, actor=self.verifier)
        self.assertEqual(row.state, ApplicabilityState.PROPOSED)
        row = applicability.countersign(row.pk, actor=self.engineer)
        self.assertEqual(row.state, ApplicabilityState.VERIFIED)

    def test_verify_requires_the_permission(self):
        row = self._proposed()
        with self.assertRaises(PermissionDenied):
            applicability.verify(row.pk, actor=self.outsider)

    def test_proposer_verification_is_refused_in_the_service_too(self):
        self.proposer.user_permissions.add(
            _permission('verify_document_applicability')
        )
        proposer = get_user_model().objects.get(pk=self.proposer.pk)
        row = self._proposed()
        with self.assertRaises(PermissionDenied):
            applicability.verify(row.pk, actor=proposer)

    def test_countersign_must_be_a_distinct_engineer(self):
        self.verifier.user_permissions.add(
            _permission('countersign_document_applicability')
        )
        verifier = get_user_model().objects.get(pk=self.verifier.pk)
        row = self._proposed(
            kind='inverter_model', target_machine_id=0, target_serial='',
            target_model='SINVERT PVS351',
        )
        applicability.verify(row.pk, actor=verifier)
        with self.assertRaises(PermissionDenied):
            applicability.countersign(row.pk, actor=verifier)

    def test_firmware_config_requires_a_payload(self):
        with self.assertRaises(applicability.ApplicabilityError):
            self._proposed(
                kind='firmware_config', target_machine_id=0, target_serial='',
                target_model='SINVERT PVS351', target_config={},
            )

    def test_revocation_needs_a_reason_and_kills_liveness(self):
        row = self._proposed()
        applicability.verify(row.pk, actor=self.verifier)
        self.assertTrue(applicability.applicability_for(self.document).exists())
        with self.assertRaises(applicability.ApplicabilityError):
            applicability.revoke(row.pk, actor=self.verifier, reason='  ')
        applicability.revoke(row.pk, actor=self.verifier, reason='wrong unit')
        self.assertFalse(applicability.applicability_for(self.document).exists())

    def test_supersession_links_and_retires(self):
        old = self._proposed()
        applicability.verify(old.pk, actor=self.verifier)
        new = self._proposed(target_machine_id=13, target_serial='EVAL-HX201')
        old = applicability.supersede(old.pk, new.pk, actor=self.verifier)
        self.assertEqual(old.state, ApplicabilityState.SUPERSEDED)
        self.assertEqual(old.superseded_by_id, new.pk)


class ResolutionTests(ApplicabilityTestCase):
    """Byte anchoring, effective windows, and the target resolver."""

    def _verified(self, **overrides):
        row = self._proposed(**overrides)
        return applicability.verify(row.pk, actor=self.verifier)

    def test_reingested_bytes_invalidate_old_verifications(self):
        self._verified()
        self.assertTrue(applicability.applicability_for(self.document).exists())
        self.document.source_sha256 = 'c' * 64
        self.document.save(update_fields=['source_sha256'])
        self.assertFalse(applicability.applicability_for(self.document).exists())
        self.assertFalse(
            applicability.verified_claims_for_targets(machine_ids=[12]).exists()
        )

    def test_effective_window_governs_liveness_per_date(self):
        self._verified(
            effective_from=datetime.date(2020, 1, 1),
            effective_to=datetime.date(2024, 12, 31),
        )
        self.assertFalse(applicability.applicability_for(self.document).exists())
        historical = applicability.applicability_for(
            self.document, on_date=datetime.date(2023, 6, 1)
        )
        self.assertTrue(historical.exists())

    def test_target_resolver_matches_each_kind(self):
        self._verified()
        model_row = self._proposed(
            kind='inverter_model', target_machine_id=0, target_serial='',
            target_model='SINVERT PVS351',
        )
        applicability.verify(model_row.pk, actor=self.verifier)
        applicability.countersign(model_row.pk, actor=self.engineer)

        by_machine = applicability.verified_claims_for_targets(machine_ids=[12])
        self.assertEqual({row.kind for row in by_machine}, {'exact_machine'})
        by_serial = applicability.verified_claims_for_targets(serials=['EVAL-HX200'])
        self.assertTrue(by_serial.exists())
        by_model = applicability.verified_claims_for_targets(
            models=['SINVERT PVS351']
        )
        self.assertEqual({row.kind for row in by_model}, {'inverter_model'})
        unmatched = applicability.verified_claims_for_targets(machine_ids=[999])
        self.assertFalse(unmatched.exists())

    def test_fleet_wide_reaches_every_target_query(self):
        row = self._proposed(
            kind='fleet_wide', target_machine_id=0, target_serial='',
        )
        applicability.verify(row.pk, actor=self.verifier)
        rows = applicability.verified_claims_for_targets(machine_ids=[999])
        self.assertEqual({entry.kind for entry in rows}, {'fleet_wide'})

    def test_safety_eligibility_is_stricter_than_current(self):
        self.assertFalse(applicability.safety_eligible(self.document))
        self._verified()
        self.assertTrue(applicability.safety_eligible(self.document))
        self.document.approved_by = None
        self.document.save(update_fields=['approved_by'])
        self.assertFalse(applicability.safety_eligible(self.document))
