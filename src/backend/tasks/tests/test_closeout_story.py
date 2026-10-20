"""P45: typed fallback reason is native capture provenance, never inferred."""

from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from tasks.closeout_models import CloseoutCapture
from tasks.services.closeout_capture import revise_capture
from tasks.tests.closeout_fixtures import CLOSEOUT_FLAGS, CloseoutEnvMixin


@override_settings(**CLOSEOUT_FLAGS)
class CloseoutStoryTests(CloseoutEnvMixin, TestCase):
    """Exercise the existing scoped writer and its immutable narrative revisions."""

    def setUp(self):
        """One scoped technician, no live services or audio."""
        self.build_env(username='typed-story')
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.url = f'/api/tasks/work-orders/{self.work_order.pk}/closeout/captures/'
        self.payload = {
            'expected_version': self.work_order.lifecycle_version,
            'idempotency_key': 'typed-fallback',
            'narrative': 'Replaced seal; initiating cause is still uncertain.',
            'voice_unavailable_reason': 'Microphone cannot be used in this area',
        }

    def test_reason_and_original_story_survive_correction_and_reload(self):
        """A typed equivalent preserves the entered reason and actual recorder."""
        response = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, 200)
        capture_id = response.data['metadata']['capture_id']
        revise_capture(
            work_order_id=self.work_order.pk,
            capture_id=capture_id,
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='typed-correction',
            expected_revision=1,
            narrative='Corrected seal account; cause remains uncertain.',
        )
        (capture,) = self.client.get(self.url).data
        self.assertEqual(
            capture['voice_unavailable_reason'],
            self.payload['voice_unavailable_reason'],
        )
        self.assertEqual(capture['created_by'], self.actor.pk)
        self.assertEqual(capture['source_type'], 'typed')
        self.assertIsNone(capture['transcript_reference'])
        self.assertEqual(
            {row['narrative'] for row in capture['revisions']},
            {
                self.payload['narrative'],
                'Corrected seal account; cause remains uncertain.',
            },
        )

    def test_exact_replay_keeps_one_capture_and_changed_reason_conflicts(self):
        """Fallback metadata is part of the original operation identity."""
        first = self.client.post(self.url, self.payload, format='json')
        replay = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data, first.data)
        conflict = self.client.post(
            self.url,
            {**self.payload, 'voice_unavailable_reason': 'Different reason'},
            format='json',
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data['code'], 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(
            CloseoutCapture.objects.filter(work_order=self.work_order).count(), 1
        )

    def test_legacy_typed_capture_does_not_invent_a_reason(self):
        """Older callers remain supported; an absent historical fact stays absent."""
        payload = {
            key: value
            for key, value in self.payload.items()
            if key != 'voice_unavailable_reason'
        }
        self.assertEqual(
            self.client.post(self.url, payload, format='json').status_code, 200
        )
        (capture,) = self.client.get(self.url).data
        self.assertEqual(capture['voice_unavailable_reason'], '')

    def test_overlong_reason_is_refused_before_any_capture(self):
        """The additive field is bounded by the existing command serializer."""
        response = self.client.post(
            self.url,
            {**self.payload, 'voice_unavailable_reason': 'x' * 501},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            CloseoutCapture.objects.filter(work_order=self.work_order).exists()
        )
