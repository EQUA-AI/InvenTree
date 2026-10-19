"""Native compare-and-set contracts used by Work draft recovery."""

from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from common.models import Attachment
from tasks.closeout_models import CloseoutCapture, CloseoutFieldDecision, CloseoutProposal
from tasks.services.closeout_capture import create_capture, proposal_decision_version
from tasks.closeout_serializers import CloseoutProposalSerializer
from tasks.tests.closeout_fixtures import CLOSEOUT_FLAGS, CloseoutEnvMixin
from tasks.tests import test_legacy_scope_api


@override_settings(**CLOSEOUT_FLAGS)
class CaptureRecoveryTest(CloseoutEnvMixin, TestCase):
    """HTTP token validation prevents same-Work-version field replacement."""

    def setUp(self):
        """Prepare an authorized capture with no extraction provider."""
        self.build_env(username='draft-recovery')
        self.api = APIClient()
        self.api.force_authenticate(self.actor)
        result = create_capture(
            work_order_id=self.work_order.pk, actor=self.actor,
            narrative='Synthetic retained narrative',
            expected_version=self.work_order.lifecycle_version, idempotency_key='capture',
        )
        self.capture_id = result.metadata['capture_id']
        self.base = f'/api/tasks/work-orders/{self.work_order.pk}/closeout/captures/'

    def token(self):
        """Read the token from the same native capture resource as the UI."""
        response = self.api.get(self.base)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data[0]['decision_version']

    def test_stale_decision_rejected_and_exact_lost_response_replayed(self):
        """Replay wins before token validation; a new intent cannot overwrite."""
        original = self.token()
        payload = {
            'expected_version': self.work_order.lifecycle_version,
            'expected_decision_version': original, 'idempotency_key': 'first-edit',
            'decisions': [{'field_path': 'action', 'decision': 'edited', 'final_value': 'First recorded action'}],
        }
        url = f'{self.base}{self.capture_id}/decisions/'
        first = self.api.post(url, payload, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertNotEqual(original, self.token())
        replay = self.api.post(url, payload, format='json')
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(first.data, replay.data)
        stale = self.api.post(url, {**payload, 'idempotency_key': 'stale-edit', 'decisions': [{'field_path': 'action', 'decision': 'edited', 'final_value': 'Invisible replacement'}]}, format='json')
        self.assertEqual(stale.status_code, 409, stale.data)
        self.assertEqual(stale.data['code'], 'CAPTURE_STALE_REVISION')
        self.assertEqual(CloseoutFieldDecision.objects.get(field_path='action').final_value, 'First recorded action')
        reviewed = self.api.post(url, {**payload, 'expected_decision_version': self.token(), 'idempotency_key': 'reviewed-edit'}, format='json')
        self.assertEqual(reviewed.status_code, 200, reviewed.data)

    def test_proposal_token_stays_bound_to_its_serialized_decision_snapshot(self):
        """A newer capture-list read cannot upgrade an older proposal's token."""
        url = f'{self.base}{self.capture_id}/decisions/'
        payload = {
            'expected_version': self.work_order.lifecycle_version,
            'expected_decision_version': self.token(), 'idempotency_key': 'snapshot-original',
            'decisions': [{'field_path': 'action', 'decision': 'edited', 'final_value': 'Reviewed original'}],
        }
        self.assertEqual(self.api.post(url, payload, format='json').status_code, 200)
        proposal = CloseoutProposal.objects.prefetch_related('decisions__decided_by').get()
        original = proposal_decision_version(proposal)
        CloseoutFieldDecision.objects.filter(proposal=proposal).update(final_value='Newer recorded value')
        snapshot = CloseoutProposalSerializer(proposal).data
        self.assertEqual(snapshot['decision_version'], original)
        self.assertEqual(snapshot['decisions'][0]['final_value'], 'Reviewed original')
        self.assertNotEqual(original, self.token())
        stale = self.api.post(url, {**payload, 'idempotency_key': 'snapshot-stale', 'expected_decision_version': snapshot['decision_version']}, format='json')
        self.assertEqual(stale.status_code, 409, stale.data)
        live = self.api.get(f'{self.base}{self.capture_id}/proposal/')
        self.assertEqual(live.data['decision_version'], self.token())
        self.assertEqual(live.data['decisions'][0]['final_value'], 'Newer recorded value')

    def test_missing_token_and_foreign_capture_are_refused(self):
        """Missing tokens fail closed and scope lookup precedes validation."""
        url = f'{self.base}{self.capture_id}/decisions/'
        self.assertEqual(self.api.post(url, {}, format='json').status_code, 400)
        self.assertEqual(self.api.post('/api/tasks/work-orders/999999/closeout/captures/1/decisions/', {}, format='json').status_code, 404)
        self.assertEqual(CloseoutCapture.objects.count(), 1)
        self.assertFalse(CloseoutFieldDecision.objects.exists())


@override_settings(AIMMS_WORK_ORDERS_ENABLED=True, AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver')
class AttachmentRecoveryTest(TestCase):
    """Work attachment metadata remains protected by native role and owner scope."""

    def setUp(self):
        """Reuse the existing real session and grant fixture."""
        test_legacy_scope_api.LegacyWorkScopeAPITest.setUp(self)
        self.attachment = Attachment.objects.create(model_type='workorder', model_id=self.own.pk, link='https://example.invalid/work', comment='Original')
        self.url = f'/api/attachment/{self.attachment.pk}/'

    def test_newer_metadata_cannot_be_overwritten_by_old_draft(self):
        """An explicit fresh token permits the intended replacement only."""
        token = self.api.get(self.url).data['expected_revision']
        missing = self.api.patch(self.url, {'comment': 'No token'}, format='json')
        self.assertEqual(missing.status_code, 409)
        first = self.api.patch(self.url, {'comment': 'Newer fact', 'expected_revision': token}, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        stale = self.api.patch(self.url, {'comment': 'Old draft', 'expected_revision': token}, format='json')
        self.assertEqual(stale.status_code, 409, stale.data)
        self.attachment.refresh_from_db()
        self.assertEqual(self.attachment.comment, 'Newer fact')
        merged = self.api.patch(self.url, {'comment': 'Deliberate merge', 'expected_revision': first.data['expected_revision']}, format='json')
        self.assertEqual(merged.status_code, 200, merged.data)

    def test_revoked_scope_hides_token_and_refuses_write(self):
        """A retained draft token grants no continuing read or write access."""
        token = self.api.get(self.url).data['expected_revision']
        self.grant.delete()
        self.assertEqual(self.api.get(self.url).status_code, 404)
        response = self.api.patch(self.url, {'comment': 'Denied', 'expected_revision': token}, format='json')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('expected_revision', response.data)
        self.attachment.refresh_from_db()
        self.assertEqual(self.attachment.comment, 'Original')
