"""WS7: governed proposal rail — allow-list, exactly-once, real receipts.

Runs under the full InvenTree settings (PostgreSQL invoke runner); it is
skipped in the minimal aichat-only settings because it exercises the real
canonical work-order command service.
"""

from __future__ import annotations

import unittest
from datetime import timedelta

from django.apps import apps

if not apps.is_installed('tasks'):
    raise unittest.SkipTest('requires the full InvenTree app registry')

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from tasks.models import KanbanCard, WorkOrderLifecycle, WorkOrderType
from tasks.scope import MaintenanceScope

from aichat.models import ChatActionProposal, ProposalState
from aichat.services import proposals as svc
from assets.models import AssetMachine
from company.models import Company
from users.models import ApiToken


class ProposalRailTestCase(TestCase):
    """Shared fixture: one scoped superuser and one in-progress work order."""

    @classmethod
    def setUpTestData(cls):
        """SetUpTestData."""
        cls.customer = Company.objects.create(
            name='Proposal Cust', is_customer=True
        )
        cls.actor = get_user_model().objects.create_superuser(
            username='proposal-sup', email='p@example.com', password='pw'
        )
        cls.machine = AssetMachine.objects.create(
            name='Press 7', customer=cls.customer
        )

    def setUp(self):
        """SetUp."""
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.work_order = KanbanCard.objects.create(
            title='Hold me',
            status=KanbanCard.STATUS_REVIEW,
            priority=KanbanCard.PRIORITY_MEDIUM,
            customer=self.customer,
            machine=self.machine,
            assigned_to=self.actor,
            work_order_type=WorkOrderType.PREVENTIVE,
            lifecycle_status=WorkOrderLifecycle.IN_PROGRESS,
        )

    def _create(self, action='work_order.hold', key='intent-1', **over):
        """Create."""
        params = {
            'owner': self.actor,
            'scope_key': f'customer:{self.customer.pk}',
            'scope_hash': 'a' * 64,
            'action_type': action,
            'work_order_id': self.work_order.pk,
            'reason': 'Spoken request: put this on hold.',
            'idempotency_key': key,
            'policy_version': 'test-v1',
        }
        params.update(over)
        return svc.create_proposal(**params)


class ProposalServiceTests(ProposalRailTestCase):
    """ProposalServiceTests."""
    def test_allow_list_is_exactly_hold_and_resume(self):
        """Allow list is exactly hold and resume."""
        self.assertEqual(
            svc.allowed_actions(), ('work_order.hold', 'work_order.resume')
        )
        with self.assertRaises(svc.CapabilityDenied):
            self._create(action='work_order.start')
        with self.assertRaises(svc.CapabilityDenied):
            self._create(action='stock.consume')

    def test_create_snapshots_server_derived_preview_and_version(self):
        """Create snapshots server derived preview and version."""
        proposal = self._create()
        self.assertEqual(proposal.state, ProposalState.PROPOSED)
        self.assertEqual(
            proposal.target_version, self.work_order.lifecycle_version
        )
        self.assertEqual(proposal.preview['current_status'], 'in_progress')
        self.assertEqual(proposal.preview['resulting_status'], 'on_hold')
        self.assertIn('does not change any safety status', proposal.preview['warning'])

    def test_create_replays_exactly_and_conflicts_on_changed_intent(self):
        """Create replays exactly and conflicts on changed intent."""
        first = self._create(key='same-key')
        replay = self._create(key='same-key')
        self.assertEqual(first.id, replay.id)
        with self.assertRaises(svc.ProposalStateConflict):
            self._create(action='work_order.resume', key='same-key')
        with self.assertRaises(svc.ProposalStateConflict):
            self._create(reason='Different hold reason.', key='same-key')
        with self.assertRaises(svc.ProposalStateConflict):
            self._create(scope_hash='b' * 64, key='same-key')

    def test_confirm_executes_the_canonical_hold_command_once(self):
        """Confirm executes the canonical hold command once."""
        proposal = self._create()
        confirmed = svc.confirm_proposal(
            owner=self.actor,
            scope_hash=proposal.scope_hash,
            proposal_id=proposal.id,
        )
        self.work_order.refresh_from_db()
        self.assertEqual(
            self.work_order.lifecycle_status, WorkOrderLifecycle.ON_HOLD
        )
        self.assertEqual(confirmed.state, ProposalState.EXECUTED)
        self.assertEqual(confirmed.receipt['command'], 'hold')
        self.assertEqual(confirmed.receipt['lifecycle_status'], 'on_hold')

        version_after_first = self.work_order.lifecycle_version
        replay = svc.confirm_proposal(
            owner=self.actor,
            scope_hash=proposal.scope_hash,
            proposal_id=proposal.id,
        )
        self.work_order.refresh_from_db()
        self.assertEqual(replay.receipt, confirmed.receipt)
        self.assertEqual(
            self.work_order.lifecycle_version,
            version_after_first,
            'replaying a confirmation must not execute a second effect',
        )

    def test_stale_target_version_fails_revalidation_without_effect(self):
        """Stale target version fails revalidation without effect."""
        proposal = self._create()
        # The work order moves on before the user confirms.
        from tasks.services.work_orders import hold_work_order

        hold_work_order(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='external-hold',
            reason='someone else held it first',
        )
        self.work_order.refresh_from_db()
        held_version = self.work_order.lifecycle_version

        with self.assertRaises(svc.ProposalRevalidationFailed):
            svc.confirm_proposal(
                owner=self.actor,
                scope_hash=proposal.scope_hash,
                proposal_id=proposal.id,
            )
        proposal.refresh_from_db()
        self.work_order.refresh_from_db()
        self.assertEqual(proposal.state, ProposalState.FAILED)
        self.assertEqual(proposal.failure_code, 'PROPOSAL_REVALIDATION_FAILED')
        self.assertEqual(self.work_order.lifecycle_version, held_version)

    def test_expired_proposal_cannot_execute(self):
        """Expired proposal cannot execute."""
        proposal = self._create()
        ChatActionProposal.objects.filter(id=proposal.id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        with self.assertRaises(svc.ProposalExpired):
            svc.confirm_proposal(
                owner=self.actor,
                scope_hash=proposal.scope_hash,
                proposal_id=proposal.id,
            )
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, ProposalState.EXPIRED)
        self.work_order.refresh_from_db()
        self.assertEqual(
            self.work_order.lifecycle_status, WorkOrderLifecycle.IN_PROGRESS
        )

    def test_reject_is_idempotent_and_blocks_confirmation(self):
        """Reject is idempotent and blocks confirmation."""
        proposal = self._create()
        svc.reject_proposal(
            owner=self.actor,
            scope_hash=proposal.scope_hash,
            proposal_id=proposal.id,
        )
        svc.reject_proposal(
            owner=self.actor,
            scope_hash=proposal.scope_hash,
            proposal_id=proposal.id,
        )
        with self.assertRaises(svc.ProposalStateConflict):
            svc.confirm_proposal(
                owner=self.actor,
                scope_hash=proposal.scope_hash,
                proposal_id=proposal.id,
            )

    def test_cross_owner_access_is_indistinguishable_from_missing(self):
        """Cross owner access is indistinguishable from missing."""
        proposal = self._create()
        stranger = get_user_model().objects.create_user(
            username='stranger', password='pw'
        )
        for operation in (svc.get_owned_proposal, svc.reject_proposal):
            with self.assertRaises(svc.ProposalNotFound):
                operation(
                    owner=stranger,
                    scope_hash=proposal.scope_hash,
                    proposal_id=proposal.id,
                )
        with self.assertRaises(svc.ProposalNotFound):
            svc.confirm_proposal(
                owner=stranger,
                scope_hash=proposal.scope_hash,
                proposal_id=proposal.id,
            )

    def test_resume_round_trip_through_the_rail(self):
        """Resume round trip through the rail."""
        hold = self._create(key='hold-key')
        svc.confirm_proposal(
            owner=self.actor,
            scope_hash=hold.scope_hash,
            proposal_id=hold.id,
        )
        self.work_order.refresh_from_db()
        resume = self._create(
            action='work_order.resume',
            key='resume-key',
            reason='Spoken request: resume the job.',
        )
        confirmed = svc.confirm_proposal(
            owner=self.actor,
            scope_hash=resume.scope_hash,
            proposal_id=resume.id,
        )
        self.work_order.refresh_from_db()
        self.assertEqual(confirmed.state, ProposalState.EXECUTED)
        self.assertEqual(
            self.work_order.lifecycle_status, WorkOrderLifecycle.IN_PROGRESS
        )

    def test_expiry_sweep_marks_only_stale_pending_rows(self):
        """Expiry sweep marks only stale pending rows."""
        stale = self._create(key='stale')
        fresh = self._create(key='fresh')
        ChatActionProposal.objects.filter(id=stale.id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        swept = svc.expire_stale_proposals()
        self.assertEqual(swept, 1)
        stale.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(stale.state, ProposalState.EXPIRED)
        self.assertEqual(fresh.state, ProposalState.PROPOSED)


def _test_scope_resolver(actor):
    """Deployment-seam resolver used by the API tests."""
    from company.models import Company

    customer = Company.objects.filter(name='Proposal Cust').first()
    if customer is None:
        return set()
    return {MaintenanceScope(customer_id=customer.pk, site_key=None)}


class ProposalApiTests(ProposalRailTestCase):
    """Session-authenticated REST rail over the same service."""

    RESOLVER = f'{__name__}._test_scope_resolver'

    def _client(self):
        """Client."""
        self.client.force_login(self.actor)
        return self.client

    def test_create_confirm_and_receipt_over_http(self):
        """Create confirm and receipt over http."""
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=self.RESOLVER):
            client = self._client()
            created = client.post(
                '/api/aichat/proposals/',
                {
                    'action_type': 'work_order.hold',
                    'work_order_id': self.work_order.pk,
                    'reason': 'hold it please',
                },
                content_type='application/json',
            )
            self.assertEqual(created.status_code, 201, created.content)
            body = created.json()
            self.assertEqual(body['state'], 'proposed')

            confirmed = client.post(
                f"/api/aichat/proposals/{body['id']}/confirm/"
            )
            self.assertEqual(confirmed.status_code, 200, confirmed.content)
            receipt = confirmed.json()['receipt']
            self.assertEqual(receipt['command'], 'hold')
            self.work_order.refresh_from_db()
            self.assertEqual(
                self.work_order.lifecycle_status, WorkOrderLifecycle.ON_HOLD
            )

    def test_unauthenticated_requests_are_rejected(self):
        """Unauthenticated requests are rejected."""
        response = self.client.post(
            '/api/aichat/proposals/',
            {'action_type': 'work_order.hold', 'work_order_id': 1},
            content_type='application/json',
        )
        self.assertIn(response.status_code, (401, 403))

    def test_api_token_cannot_confirm_visual_proposal(self):
        """Api token cannot confirm visual proposal."""
        proposal = self._create()
        token = ApiToken.objects.create(user=self.actor, name='proposal-api-token')

        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=self.RESOLVER):
            response = self.client.post(
                f'/api/aichat/proposals/{proposal.id}/confirm/',
                HTTP_AUTHORIZATION=f'Token {token.key}',
            )

        self.assertIn(response.status_code, (401, 403))
        self.work_order.refresh_from_db()
        self.assertEqual(
            self.work_order.lifecycle_status, WorkOrderLifecycle.IN_PROGRESS
        )

    def test_cross_scope_target_is_not_disclosed_on_create(self):
        """Cross scope target is not disclosed on create."""
        other_customer = Company.objects.create(
            name='Proposal Other Customer', is_customer=True
        )
        other_machine = AssetMachine.objects.create(
            name='Secret press', customer=other_customer
        )
        other_work_order = KanbanCard.objects.create(
            title='Secret work order',
            status=KanbanCard.STATUS_REVIEW,
            priority=KanbanCard.PRIORITY_MEDIUM,
            customer=other_customer,
            machine=other_machine,
            work_order_type=WorkOrderType.PREVENTIVE,
            lifecycle_status=WorkOrderLifecycle.IN_PROGRESS,
        )

        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=self.RESOLVER):
            response = self._client().post(
                '/api/aichat/proposals/',
                {
                    'action_type': 'work_order.hold',
                    'work_order_id': other_work_order.pk,
                    'reason': 'cross-scope probe',
                },
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error'], 'PROPOSAL_NOT_FOUND')
        self.assertFalse(
            ChatActionProposal.objects.filter(
                owner=self.actor, target_work_order_id=other_work_order.pk
            ).exists()
        )

    def test_scope_revocation_hides_existing_proposal(self):
        """Scope revocation hides existing proposal."""
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=self.RESOLVER):
            created = self._client().post(
                '/api/aichat/proposals/',
                {
                    'action_type': 'work_order.hold',
                    'work_order_id': self.work_order.pk,
                    'reason': 'scope revocation probe',
                },
                content_type='application/json',
            )
        self.assertEqual(created.status_code, 201)
        proposal_id = created.json()['id']

        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda _actor: set()):
            listed = self.client.get('/api/aichat/proposals/')
            detailed = self.client.get(f'/api/aichat/proposals/{proposal_id}/')
            replayed = self.client.post(
                f'/api/aichat/proposals/{proposal_id}/confirm/'
            )

        self.assertEqual(listed.status_code, 403)
        self.assertEqual(detailed.status_code, 404)
        self.assertEqual(replayed.status_code, 404)

    def test_disallowed_action_is_denied_over_http(self):
        """Disallowed action is denied over http."""
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=self.RESOLVER):
            client = self._client()
            response = client.post(
                '/api/aichat/proposals/',
                {
                    'action_type': 'work_order.complete',
                    'work_order_id': self.work_order.pk,
                },
                content_type='application/json',
            )
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()['error'], 'CAPABILITY_DENIED')
