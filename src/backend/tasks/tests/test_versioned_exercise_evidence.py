"""A new prepared observation cannot rewrite a previously recorded sheet."""

import hashlib
import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings

from rest_framework.exceptions import ValidationError

from common.models import Attachment
from repair.pump08_exercise import (
    DEFAULT_EVIDENCE_PROFILE,
    TECH_A_V9_EVIDENCE_PROFILE,
    evidence_bytes,
)
from tasks.exercise_verification_api import attach_prepared_evidence
from tasks.models import WorkOrderCommand
from tasks.services.work_orders import IdempotencyConflict
from tasks.tests import test_pump08_exercise as fixture_module
from tasks.work_execution.exercise_verification import _evidence_registry

LEGACY_E03_SHA256 = 'bb30cc0d8e44c1d129358bf73a1bbc4ed36d222f042421e0ce04d2a825d7edec'


@override_settings(
    AIMMS_PROCEDURES_ENABLED=True,
    AIMMS_TEST_ALARMS_ENABLED=True,
    AIMMS_WORK_ORDERS_ENABLED=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
)
class VersionedExerciseEvidenceTest(TestCase):
    """Use actual source ownership, native commands, files and hash validation."""

    def fixture(self, profile=DEFAULT_EVIDENCE_PROFILE):
        """Select the profile only at the existing explicit native attach action."""
        def attach(work_order_id, actor, key):
            return attach_prepared_evidence(work_order_id, actor, key, profile=profile)

        with patch.object(fixture_module, 'attach_prepared_evidence', side_effect=attach):
            fixture_module.Pump08ExerciseTest.setUp(self)
        self.registry_event = self.work.events.get(event_type='EXERCISE_EVIDENCE_ATTACHED')
        evidence = self.registry_event.metadata['result_metadata']['evidence']
        self.e03 = Attachment.objects.get(pk=evidence['P08-TEMP-E03']['id'])

    def test_default_bytes_and_original_registry_remain_valid(self):
        self.fixture()
        self.assertEqual(hashlib.sha256(evidence_bytes('P08-TEMP-E03')).hexdigest(), LEGACY_E03_SHA256)
        self.assertEqual(_evidence_registry(self.work)['P08-TEMP-E03']['id'], self.e03.pk)
        with self.e03.attachment.open('rb') as source:
            self.assertEqual(hashlib.sha256(source.read()).hexdigest(), LEGACY_E03_SHA256)

    def test_v9_profile_is_explicit_prepared_observation_with_actual_receipt(self):
        self.fixture(TECH_A_V9_EVIDENCE_PROFILE)
        metadata = self.registry_event.metadata['result_metadata']
        self.assertEqual(metadata['profile'], TECH_A_V9_EVIDENCE_PROFILE)
        self.assertEqual(self.registry_event.actor_id, self.actor.pk)
        self.assertEqual(self.e03.model_id, self.work.pk)
        with self.e03.attachment.open('rb') as source:
            content = source.read()
        self.assertIn(b'no visible component damage', content)
        self.assertIn(b'not an actual field inspection', content)
        self.assertIn(b'Template profile: tech-a-v9', content)
        self.assertNotEqual(hashlib.sha256(content).hexdigest(), LEGACY_E03_SHA256)
        self.assertEqual(content, evidence_bytes('P08-TEMP-E03', profile=TECH_A_V9_EVIDENCE_PROFILE))
        self.assertEqual(metadata['evidence']['P08-TEMP-E03']['sha256'], hashlib.sha256(content).hexdigest())
        self.assertEqual(_evidence_registry(self.work)['P08-TEMP-E03']['id'], self.e03.pk)
        self.assertIsNone(self.work.actual_started_at)

    def test_new_profile_cannot_replace_existing_default_evidence(self):
        self.fixture()
        count = Attachment.objects.count()
        with self.assertRaises(ValidationError):
            attach_prepared_evidence(
                self.work.pk, self.actor, uuid.uuid4().hex,
                profile=TECH_A_V9_EVIDENCE_PROFILE,
            )
        self.assertEqual(Attachment.objects.count(), count)
        self.assertEqual(self.work.events.filter(event_type='EXERCISE_EVIDENCE_ATTACHED').count(), 1)
        self.assertEqual(_evidence_registry(self.work)['P08-TEMP-E03']['id'], self.e03.pk)

    def test_recorded_profile_and_bytes_must_agree(self):
        self.fixture(TECH_A_V9_EVIDENCE_PROFILE)
        self.registry_event.metadata['result_metadata']['profile'] = DEFAULT_EVIDENCE_PROFILE
        self.registry_event.save(update_fields=['metadata'])
        self.assertNotIn('P08-TEMP-E03', _evidence_registry(self.work))

    def test_command_key_cannot_change_profile(self):
        self.fixture()
        key = WorkOrderCommand.objects.get(work_order=self.work, command='exercise_evidence').idempotency_key
        with self.assertRaises(IdempotencyConflict):
            attach_prepared_evidence(
                self.work.pk, self.actor, key, profile=TECH_A_V9_EVIDENCE_PROFILE,
            )

    def test_native_api_rejects_unknown_profile_without_new_evidence(self):
        self.fixture()
        count = Attachment.objects.count()
        response = self.api.post(
            f'/api/tasks/work-orders/{self.work.pk}/exercise-verification/evidence/',
            {'idempotency_key': uuid.uuid4().hex, 'profile': 'unknown-profile'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Attachment.objects.count(), count)
