"""Scope, flag-gating, permission, and HTTP contract tests for closeout."""

from django.test import TestCase, override_settings

from rest_framework import status
from rest_framework.test import APIClient

from company.models import Company
from tasks.models import WorkOrderCloseout, WorkOrderLifecycle
from tasks.scope import MaintenanceScope
from tasks.tests.closeout_fixtures import (
    CLOSEOUT_FLAGS,
    VALID_CLOSEOUT,
    CloseoutEnvMixin,
)

CLOSEOUT_ROUTES = (
    'closeout/captures/',
    'closeout/part-usage/',
    'closeout/readings/',
    'closeout/effects/',
    'closeout/amendments/',
)


@override_settings(**CLOSEOUT_FLAGS)
class CloseoutScopeSecurityTest(CloseoutEnvMixin, TestCase):
    """Cross-scope actors learn nothing; flags fail closed as 404."""

    def setUp(self):
        self.build_env(username='security-owner')
        self.client = APIClient()
        self.client.force_authenticate(self.actor)

    def url(self, suffix):
        return f'/api/tasks/work-orders/{self.work_order.pk}/{suffix}'

    def test_unauthenticated_requests_are_rejected(self):
        anonymous = APIClient()
        for suffix in CLOSEOUT_ROUTES:
            response = anonymous.get(self.url(suffix))
            self.assertIn(
                response.status_code,
                (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
                suffix,
            )

    def test_cross_scope_actor_gets_scope_safe_404(self):
        other_customer = Company.objects.create(name='Other Co', is_customer=True)
        outsider = self.make_scoped_user(
            'security-outsider',
            permissions=[
                'capture_closeout',
                'review_closeout',
                'reconcile_closeout_parts',
            ],
        )
        outsider.maintenance_scopes = {
            MaintenanceScope(customer_id=other_customer.pk, site_key=None)
        }
        client = APIClient()
        client.force_authenticate(outsider)
        for suffix in CLOSEOUT_ROUTES:
            response = client.get(self.url(suffix))
            self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, suffix)
        response = client.post(
            self.url('closeout/captures/'),
            {
                'expected_version': 1,
                'idempotency_key': 'outsider-1',
                'narrative': 'I should not see this work order',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(AIMMS_CLOSEOUT_WIZARD_ENABLED=False)
    def test_disabled_wizard_hides_every_route(self):
        for suffix in CLOSEOUT_ROUTES:
            response = self.client.get(self.url(suffix))
            self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, suffix)

    def test_missing_permission_is_403_not_404(self):
        viewer = self.make_scoped_user('security-viewer')
        client = APIClient()
        client.force_authenticate(viewer)
        response = client.post(
            self.url('closeout/captures/'),
            {
                'expected_version': self.work_order.lifecycle_version,
                'idempotency_key': 'viewer-1',
                'narrative': 'trying without permission',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data['code'], 'PERMISSION_DENIED')

    def test_scoped_reads_return_empty_not_leaky(self):
        response = self.client.get(self.url('closeout/captures/'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])


@override_settings(**CLOSEOUT_FLAGS)
class CloseoutHTTPFlowTest(CloseoutEnvMixin, TestCase):
    """Wizard happy path over HTTP: capture -> decisions -> complete."""

    def setUp(self):
        self.build_env(username='http-flow')
        self.client = APIClient()
        self.client.force_authenticate(self.actor)

    def url(self, suffix):
        return f'/api/tasks/work-orders/{self.work_order.pk}/{suffix}'

    def test_capture_decide_complete_flow(self):
        created = self.client.post(
            self.url('closeout/captures/'),
            {
                'expected_version': self.work_order.lifecycle_version,
                'idempotency_key': 'http-cap',
                'narrative': 'Replaced the clogged filter; flow back to 20 GPM.',
            },
            format='json',
        )
        self.assertEqual(created.status_code, status.HTTP_200_OK)
        capture_id = created.data['metadata']['capture_id']

        listed = self.client.get(self.url('closeout/captures/'))
        self.assertEqual(listed.data[0]['id'], capture_id)
        self.assertEqual(listed.data[0]['status'], 'open')

        decided = self.client.post(
            self.url(f'closeout/captures/{capture_id}/decisions/'),
            {
                'expected_version': self.work_order.lifecycle_version,
                'idempotency_key': 'http-dec',
                'decisions': [
                    {
                        'field_path': name,
                        'decision': 'edited',
                        'final_value': VALID_CLOSEOUT[name],
                    }
                    for name in ('action', 'result', 'verification_summary')
                ],
            },
            format='json',
        )
        self.assertEqual(decided.status_code, status.HTTP_200_OK)
        self.assertEqual(decided.data['metadata']['capture_status'], 'reviewed')

        readiness = self.client.get(self.url('readiness/?action=complete'))
        self.assertTrue(readiness.data['ready'])

        completed = self.client.post(
            self.url('complete/'),
            {
                'expected_version': self.work_order.lifecycle_version,
                'idempotency_key': 'http-complete',
                'capture_id': capture_id,
                **VALID_CLOSEOUT,
            },
            format='json',
        )
        self.assertEqual(completed.status_code, status.HTTP_200_OK)
        self.assertEqual(
            completed.data['lifecycle_status'], WorkOrderLifecycle.COMPLETED
        )
        closeout = WorkOrderCloseout.objects.get(work_order=self.work_order)
        self.assertEqual(closeout.action, VALID_CLOSEOUT['action'])

        effects = self.client.get(self.url('closeout/effects/'))
        self.assertEqual(effects.status_code, status.HTTP_200_OK)
        self.assertEqual(len(effects.data), 1)
        self.assertEqual(effects.data[0]['effect_type'], 'notification')

    def test_unreviewed_capture_completion_conflicts_with_blockers(self):
        created = self.client.post(
            self.url('closeout/captures/'),
            {
                'expected_version': self.work_order.lifecycle_version,
                'idempotency_key': 'http-cap-open',
                'narrative': 'in progress...',
            },
            format='json',
        )
        capture_id = created.data['metadata']['capture_id']
        completed = self.client.post(
            self.url('complete/'),
            {
                'expected_version': self.work_order.lifecycle_version,
                'idempotency_key': 'http-complete-blocked',
                'capture_id': capture_id,
                **VALID_CLOSEOUT,
            },
            format='json',
        )
        self.assertEqual(completed.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(completed.data['code'], 'CLOSEOUT_REQUIRED')
        self.assertTrue(completed.data['blockers'])

    def test_proposal_route_404s_before_extraction(self):
        created = self.client.post(
            self.url('closeout/captures/'),
            {
                'expected_version': self.work_order.lifecycle_version,
                'idempotency_key': 'http-cap-noprop',
                'narrative': 'no proposal yet',
            },
            format='json',
        )
        capture_id = created.data['metadata']['capture_id']
        response = self.client.get(
            self.url(f'closeout/captures/{capture_id}/proposal/')
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_reading_and_part_usage_routes_round_trip(self):
        reading = self.client.post(
            self.url('closeout/readings/'),
            {
                'label': 'Output pressure',
                'raw_text': '42 psi',
                'unit': 'psi',
                'expected_min': '40',
                'expected_max': '45',
            },
            format='json',
        )
        self.assertEqual(reading.status_code, status.HTTP_201_CREATED)
        self.assertEqual(reading.data['verification_state'], 'verified')

        refreshed = self.client.post(self.url('closeout/part-usage/refresh/'), {})
        self.assertEqual(refreshed.status_code, status.HTTP_200_OK)
        self.assertEqual(refreshed.data['created'], 0)

        candidate = self.client.post(
            self.url('closeout/part-usage/'),
            {'kind': 'candidate', 'candidate_text': 'a 30A contactor'},
            format='json',
        )
        self.assertEqual(candidate.status_code, status.HTTP_201_CREATED)
        resolve = self.client.post(
            self.url(f'closeout/part-usage/{candidate.data["id"]}/resolve/'),
            {'disposition': 'dismissed', 'reason': 'not actually used'},
            format='json',
        )
        self.assertEqual(resolve.status_code, status.HTTP_200_OK)
        self.assertEqual(resolve.data['state'], 'reconciled')

    def test_stale_version_maps_to_conflict(self):
        response = self.client.post(
            self.url('closeout/captures/'),
            {
                'expected_version': self.work_order.lifecycle_version + 5,
                'idempotency_key': 'http-stale',
                'narrative': 'stale',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'STALE_VERSION')
