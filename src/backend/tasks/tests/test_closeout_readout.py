"""P24: source revisions and original accepted facts survive read projections."""

from django.test import TestCase, override_settings

from rest_framework.test import APIClient

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
        original.refresh_from_db()
        self.assertEqual(original.result, VALID_CLOSEOUT['result'])
