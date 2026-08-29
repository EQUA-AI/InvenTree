"""Immutability, verification, and amendment governance tests."""

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse

from tasks.closeout_models import CloseoutAmendment, CloseoutAmendmentStatus
from tasks.models import WorkOrderCloseout, WorkOrderEvent
from tasks.services.closeout import complete_work_order
from tasks.services.closeout_amend import (
    AmendmentError,
    AmendmentPolicyRequired,
    VerificationError,
    decide_amendment,
    effective_closeout,
    effective_closeout_overview,
    propose_amendment,
    verify_closeout,
)
from tasks.tests.closeout_fixtures import (
    CLOSEOUT_FLAGS,
    VALID_CLOSEOUT,
    CloseoutEnvMixin,
)

AMEND_FLAGS = dict(
    CLOSEOUT_FLAGS,
    AIMMS_CLOSEOUT_AMENDMENTS_ENABLED=True,
    AIMMS_CLOSEOUT_VERIFY_POLICY='optional',
)


class CompletedCloseoutMixin(CloseoutEnvMixin):
    """Complete one work order so a closeout row exists."""

    def build_completed(self, username):
        self.build_env(username=username)
        complete_work_order(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='complete-amend',
            closeout=VALID_CLOSEOUT,
        )
        self.work_order.refresh_from_db()
        self.closeout = WorkOrderCloseout.objects.get(work_order=self.work_order)


@override_settings(**AMEND_FLAGS)
class CloseoutImmutabilityTest(CompletedCloseoutMixin, TestCase):
    """FR-CO-013/014: completed rows are byte-stable through app paths."""

    def setUp(self):
        self.build_completed('immutable-user')

    def test_destructive_save_is_rejected(self):
        self.closeout.action = 'rewritten history'
        with self.assertRaises(ValueError):
            self.closeout.save()
        with self.assertRaises(ValueError):
            self.closeout.save(update_fields=['action'])

    def test_verification_carveout_is_allowed(self):
        from django.utils import timezone

        self.closeout.verified_by = self.actor
        self.closeout.verified_at = timezone.now()
        self.closeout.save(update_fields=['verified_by', 'verified_at'])


@override_settings(**AMEND_FLAGS)
class VerifyCloseoutTest(CompletedCloseoutMixin, TestCase):
    """One-shot verification with completer/verifier separation."""

    def setUp(self):
        self.build_completed('verify-user')
        self.supervisor = self.make_scoped_user(
            'verify-sup', permissions=['verify_closeout']
        )

    def verify(self, actor, key='verify-1'):
        return verify_closeout(
            work_order_id=self.work_order.pk,
            actor=actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=key,
        )

    def test_supervisor_verifies_once(self):
        self.verify(self.supervisor)
        self.closeout.refresh_from_db()
        self.assertEqual(self.closeout.verified_by_id, self.supervisor.pk)
        self.assertIsNotNone(self.closeout.verified_at)
        self.assertTrue(
            WorkOrderEvent.objects.filter(
                work_order=self.work_order, event_type='CLOSEOUT_VERIFIED'
            ).exists()
        )

    def test_completer_cannot_verify_when_separated(self):
        with self.assertRaises(VerificationError):
            self.verify(self.actor)

    @override_settings(AIMMS_CLOSEOUT_VERIFY_SEPARATION=False)
    def test_separation_is_configurable(self):
        self.verify(self.actor)
        self.closeout.refresh_from_db()
        self.assertEqual(self.closeout.verified_by_id, self.actor.pk)

    def test_second_verification_is_rejected(self):
        self.verify(self.supervisor)
        with self.assertRaises(VerificationError):
            self.verify(self.supervisor, key='verify-2')

    def test_replay_returns_original_receipt(self):
        first = self.verify(self.supervisor, key='same')
        replay = self.verify(self.supervisor, key='same')
        self.assertEqual(first.event_id, replay.event_id)

    @override_settings(AIMMS_CLOSEOUT_VERIFY_POLICY='off')
    def test_policy_off_rejects_verification(self):
        with self.assertRaises(VerificationError):
            self.verify(self.supervisor)

    def test_verifier_needs_permission(self):
        technician = self.make_scoped_user(
            'verify-tech', permissions=['capture_closeout']
        )
        with self.assertRaises(PermissionDenied):
            self.verify(technician)


@override_settings(**AMEND_FLAGS)
class AmendmentTest(CompletedCloseoutMixin, TestCase):
    """Append-only corrections under supervisor policy."""

    def setUp(self):
        self.build_completed('amend-user')
        self.requester = self.make_scoped_user(
            'amend-req', permissions=['amend_closeout']
        )
        self.approver = self.make_scoped_user(
            'amend-sup', permissions=['verify_closeout', 'amend_closeout']
        )

    def propose(self, changes=None, key='amend-1', actor=None):
        return propose_amendment(
            work_order_id=self.work_order.pk,
            actor=actor or self.requester,
            changes=changes or {'result': {'to': 'Restored flow (corrected: 22 GPM)'}},
            reason='Technician misread the gauge',
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=key,
        )

    def decide(self, amendment_id, *, approve, actor=None, key='decide-1'):
        return decide_amendment(
            work_order_id=self.work_order.pk,
            amendment_id=amendment_id,
            actor=actor or self.approver,
            approve=approve,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=key,
        )

    def test_amendment_applies_without_touching_the_original(self):
        original_hash = self.closeout.content_hash
        original_result = self.closeout.result
        proposal = self.propose()
        amendment_id = proposal.metadata['amendment_id']
        self.decide(amendment_id, approve=True)

        amendment = CloseoutAmendment.objects.get(pk=amendment_id)
        self.assertEqual(amendment.status, CloseoutAmendmentStatus.APPLIED)
        self.assertEqual(
            amendment.effective_snapshot['closeout']['result'],
            'Restored flow (corrected: 22 GPM)',
        )
        self.assertTrue(amendment.effective_snapshot_hash)
        self.assertIn('asset_history', amendment.effective_snapshot)

        self.closeout.refresh_from_db()
        self.assertEqual(self.closeout.content_hash, original_hash)
        self.assertEqual(self.closeout.result, original_result)
        self.assertEqual(
            effective_closeout(self.closeout)['result'],
            'Restored flow (corrected: 22 GPM)',
        )
        self.assertTrue(
            WorkOrderEvent.objects.filter(
                work_order=self.work_order, event_type='AMENDED'
            ).exists()
        )

    def test_requester_cannot_decide_their_own_amendment(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        proposal = self.propose()
        # Even with full decision authority, the requester is separated out.
        self.requester.user_permissions.add(
            Permission.objects.get(
                codename='verify_closeout', content_type__app_label='tasks'
            )
        )
        empowered = get_user_model().objects.get(pk=self.requester.pk)
        empowered.maintenance_scopes = self.requester.maintenance_scopes
        with self.assertRaises(AmendmentError):
            decide_amendment(
                work_order_id=self.work_order.pk,
                amendment_id=proposal.metadata['amendment_id'],
                actor=empowered,
                approve=True,
                expected_version=self.work_order.lifecycle_version,
                idempotency_key='self-decide',
            )

    def test_rejected_amendment_changes_nothing(self):
        proposal = self.propose()
        amendment_id = proposal.metadata['amendment_id']
        self.decide(amendment_id, approve=False)
        amendment = CloseoutAmendment.objects.get(pk=amendment_id)
        self.assertEqual(amendment.status, CloseoutAmendmentStatus.REJECTED)
        self.assertIsNone(amendment.effective_snapshot)
        self.assertEqual(
            effective_closeout(self.closeout)['result'], VALID_CLOSEOUT['result']
        )

    def test_unamendable_field_is_rejected(self):
        with self.assertRaises(AmendmentError):
            self.propose(changes={'content_hash': {'to': 'forged'}})

    def test_amendment_cannot_blank_required_field(self):
        proposal = self.propose(changes={'action': {'to': '  '}}, key='blank-req')
        with self.assertRaises(AmendmentError):
            self.decide(proposal.metadata['amendment_id'], approve=True)

    @override_settings(AIMMS_CLOSEOUT_AMENDMENT_APPROVAL='approvals')
    def test_approvals_policy_fails_closed(self):
        proposal = self.propose(key='approvals-mode')
        with self.assertRaises(AmendmentPolicyRequired):
            self.decide(proposal.metadata['amendment_id'], approve=True)

    @override_settings(AIMMS_CLOSEOUT_AMENDMENTS_ENABLED=False)
    def test_disabled_amendments_fail_closed(self):
        with self.assertRaises(AmendmentError):
            self.propose(key='disabled')

    def test_applied_amendment_creates_fresh_notification_intent(self):
        from tasks.closeout_models import CloseoutEffect

        proposal = self.propose()
        amendment_id = proposal.metadata['amendment_id']
        self.decide(amendment_id, approve=True)
        self.assertTrue(
            CloseoutEffect.objects.filter(
                effect_key=(
                    f'closeout:{self.closeout.pk}:notification:'
                    f'amendment:{amendment_id}'
                )
            ).exists()
        )

    def test_second_amendment_overlays_the_first(self):
        first = self.propose()
        self.decide(first.metadata['amendment_id'], approve=True)
        second = self.propose(
            changes={'cause': {'to': 'Bearing wear, not filter'}}, key='amend-2'
        )
        self.decide(second.metadata['amendment_id'], approve=True, key='decide-2')
        effective = effective_closeout(self.closeout)
        self.assertEqual(effective['cause'], 'Bearing wear, not filter')
        self.assertEqual(effective['result'], 'Restored flow (corrected: 22 GPM)')


@override_settings(**AMEND_FLAGS)
class OverviewEffectiveCloseoutTest(CompletedCloseoutMixin, TestCase):
    """The REST overview projects the effective closeout, with provenance."""

    AMENDED_RESULT = 'Restored flow (corrected: 22 GPM)'

    def setUp(self):
        self.build_completed('overview-amend-user')
        self.requester = self.make_scoped_user(
            'overview-req', permissions=['amend_closeout']
        )
        self.approver = self.make_scoped_user(
            'overview-sup', permissions=['verify_closeout', 'amend_closeout']
        )
        self.url = reverse(
            'kanban-card-overview', kwargs={'pk': self.work_order.pk}
        )
        self.client.force_login(self.actor)

    def overview_closeout(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return response.json()['structured_closeout']

    def amend(self, *, approve, key='overview-amend'):
        proposal = propose_amendment(
            work_order_id=self.work_order.pk,
            actor=self.requester,
            changes={'result': {'to': self.AMENDED_RESULT}},
            reason='Technician misread the gauge',
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=key,
        )
        decide_amendment(
            work_order_id=self.work_order.pk,
            amendment_id=proposal.metadata['amendment_id'],
            actor=self.approver,
            approve=approve,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=f'decide-{key}',
        )

    def test_unamended_payload_matches_the_base_row(self):
        payload = self.overview_closeout()
        for field, value in VALID_CLOSEOUT.items():
            self.assertEqual(payload[field], value)
        self.assertFalse(payload['amended'])
        self.assertEqual(payload['amendment_count'], 0)

    def test_applied_amendment_supersedes_and_is_visible(self):
        self.amend(approve=True)
        payload = self.overview_closeout()
        self.assertEqual(payload['result'], self.AMENDED_RESULT)
        self.assertEqual(payload['cause'], VALID_CLOSEOUT['cause'])
        self.assertTrue(payload['amended'])
        self.assertEqual(payload['amendment_count'], 1)

    def test_rejected_amendment_leaves_raw_values(self):
        self.amend(approve=False)
        payload = self.overview_closeout()
        self.assertEqual(payload['result'], VALID_CLOSEOUT['result'])
        self.assertFalse(payload['amended'])
        self.assertEqual(payload['amendment_count'], 0)

    def test_applied_amendments_prefers_the_prefetched_attr(self):
        self.amend(approve=True)
        closeout = WorkOrderCloseout.objects.get(pk=self.closeout.pk)
        # Simulate the list-endpoint Prefetch(to_attr='applied_amendments'):
        # an (empty) prefetched list must win over the real applied row, with
        # zero queries.
        closeout.applied_amendments = []
        with self.assertNumQueries(0):
            overview = effective_closeout_overview(closeout)
        self.assertEqual(overview['result'], VALID_CLOSEOUT['result'])
        self.assertFalse(overview['amended'])
