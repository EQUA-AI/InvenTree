"""P24: source revisions and original accepted facts survive read projections."""

from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIClient

from tasks.closeout_models import (
    CloseoutCapture,
    CloseoutFieldDecision,
    CloseoutProposal,
)
from tasks.models import WorkOrderCloseout
from tasks.serializers import WorkOrderOverviewSerializer
from tasks.services.closeout import complete_work_order
from tasks.services.closeout_amend import decide_amendment, propose_amendment
from tasks.services.closeout_capture import create_capture, revise_capture
from tasks.tests.closeout_fixtures import CLOSEOUT_FLAGS, VALID_CLOSEOUT, CloseoutEnvMixin


@override_settings(**CLOSEOUT_FLAGS)
class CloseoutReadoutTests(CloseoutEnvMixin, TestCase):
    """Use native commands; reads must neither accept nor overwrite sources."""

    def setUp(self):
        """One authorized work order."""
        self.build_env(username='closeout-readout')
        self.actor.first_name = 'Synthetic'
        self.actor.last_name = 'Technician'
        self.actor.save()
        self.client = APIClient()
        self.client.force_authenticate(self.actor)

    def test_retained_revisions_are_returned_with_source_and_attribution(self):
        """A correction appends; source text and its original hash remain."""
        created = create_capture(
            work_order_id=self.work_order.pk, actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='story-original', narrative='Original story',
        )
        capture_id = created.metadata['capture_id']
        revise_capture(
            work_order_id=self.work_order.pk, capture_id=capture_id,
            actor=self.actor, expected_version=self.work_order.lifecycle_version,
            idempotency_key='story-corrected', expected_revision=1,
            narrative='Corrected story',
        )
        response = self.client.get(f'/api/tasks/work-orders/{self.work_order.pk}/closeout/captures/')
        self.assertEqual(response.status_code, 200)
        (capture,) = response.data
        self.assertEqual(capture['revision'], 2)
        self.assertEqual(capture['narrative'], 'Corrected story')
        revisions = sorted(capture['revisions'], key=lambda row: row['revision'])
        self.assertEqual([r['narrative'] for r in revisions], ['Original story', 'Corrected story'])
        self.assertEqual(revisions[0]['created_by'], self.actor.pk)
        self.assertEqual(capture['created_by_name'], 'Synthetic Technician')
        self.assertEqual(revisions[0]['created_by_name'], 'Synthetic Technician')
        self.assertEqual(revisions[1]['created_by_name'], 'Synthetic Technician')
        self.assertTrue(revisions[0]['source_content_hash'])
        self.assertEqual(revisions[1]['supersedes'], revisions[0]['id'])
        self.assertEqual(capture['status'], 'open')

    @override_settings(AIMMS_CLOSEOUT_AMENDMENTS_ENABLED=True, AIMMS_CLOSEOUT_AMENDMENT_APPROVAL='self')
    def test_effective_account_keeps_original_snapshot_and_hash(self):
        """An amendment changes the projection, never the accepted base row."""
        complete_work_order(
            work_order_id=self.work_order.pk, actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='complete-readout', closeout=VALID_CLOSEOUT,
        )
        self.work_order.refresh_from_db()
        original = WorkOrderCloseout.objects.get(work_order=self.work_order)
        proposal = propose_amendment(
            work_order_id=self.work_order.pk, actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='amend-readout', changes={'result': 'Corrected result'},
            reason='Clarified observation',
        )
        decide_amendment(
            work_order_id=self.work_order.pk, actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='apply-readout', amendment_id=proposal.metadata['amendment_id'],
            approve=True,
        )
        self.work_order.refresh_from_db()
        body = WorkOrderOverviewSerializer.get_structured_closeout(self.work_order)
        self.assertEqual(body['result'], 'Corrected result')
        self.assertEqual(body['original']['result'], VALID_CLOSEOUT['result'])
        self.assertEqual(body['content_hash'], original.content_hash)
        self.assertEqual(body['completed_by'], self.actor.pk)
        response = self.client.get(f'/api/tasks/work-orders/{self.work_order.pk}/closeout/amendments/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['requested_by_name'], 'Synthetic Technician')
        self.assertEqual(response.data[0]['decided_by_name'], 'Synthetic Technician')
        original.refresh_from_db()
        self.assertEqual(original.result, VALID_CLOSEOUT['result'])


    def test_saved_decision_carries_display_name_and_username_fallback(self):
        """Every account value keeps the explicit decision's recorded person."""
        created = create_capture(
            work_order_id=self.work_order.pk, actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='decision-name', narrative='Original story',
        )
        capture = CloseoutCapture.objects.get(pk=created.metadata['capture_id'])
        proposal = CloseoutProposal.objects.create(
            capture_revision=capture.current_revision, schema_version=1, fields={},
            extractor='test', content_hash='d' * 64,
        )
        CloseoutFieldDecision.objects.create(
            proposal=proposal, field_path='action', origin='extracted',
            decision='accepted', final_value='Recorded action',
            decided_by=self.actor, decided_at=timezone.now(),
        )
        url = f'/api/tasks/work-orders/{self.work_order.pk}/closeout/captures/{capture.pk}/proposal/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['decisions'][0]['decided_by_name'], 'Synthetic Technician')
        self.actor.first_name = self.actor.last_name = ''
        self.actor.save()
        self.assertEqual(self.client.get(url).data['decisions'][0]['decided_by_name'], self.actor.username)
