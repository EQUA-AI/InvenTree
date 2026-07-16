"""WS8 re-cut: capture REST rail (full InvenTree settings only)."""

from __future__ import annotations

import hashlib
import json
import unittest
from unittest.mock import patch

from django.apps import apps

if not apps.is_installed('rest_framework'):
    raise unittest.SkipTest('requires the full InvenTree app registry')

import os

from django.contrib.auth import get_user_model
from django.test import TestCase

from tasks.models import KanbanCard, WorkOrderLifecycle, WorkOrderType

from assets.models import AssetMachine
from company.models import Company
from users.models import ApiToken
from voice.models import VoiceCaptureSession

CAPTURE_ENV = {
    'AIMMS_SINGLE_SITE_POLICY_KEY': 'epcon-pilot',
    'AIMMS_VOICE_CAPTURE_ENABLED': '1',
    'AIMMS_VOICE_PURPOSES': 'fault_intake',
    'AIMMS_VOICE_CONSENT_VERSION': 'consent-v1',
}


class CaptureApiTests(TestCase):
    """CaptureApiTests."""
    @classmethod
    def setUpTestData(cls):
        """SetUpTestData."""
        cls.tech = get_user_model().objects.create_user(
            username='cap-api-tech', password='pw'
        )
        cls.customer = Company.objects.create(
            name='Capture API Customer', is_customer=True
        )
        cls.machine = AssetMachine.objects.create(
            name='Capture API Machine', customer=cls.customer
        )
        cls.work_order = KanbanCard.objects.create(
            title='Capture API Work Order',
            status=KanbanCard.STATUS_REVIEW,
            priority=KanbanCard.PRIORITY_MEDIUM,
            customer=cls.customer,
            machine=cls.machine,
            work_order_type=WorkOrderType.PREVENTIVE,
            lifecycle_status=WorkOrderLifecycle.IN_PROGRESS,
        )

    def _client(self):
        """Client."""
        self.client.force_login(self.tech)
        return self.client

    def _env(self):
        """Env."""
        return {
            **CAPTURE_ENV,
            'AIMMS_VOICE_PILOT_USER_IDS': json.dumps([self.tech.pk]),
        }

    def _create(self, client, purpose='fault_intake'):
        """Create."""
        return client.post(
            '/api/voice/captures/',
            {
                'purpose': purpose,
                'work_order_id': self.work_order.pk,
                'work_order_version': self.work_order.lifecycle_version,
            },
            content_type='application/json',
        )

    def test_capture_disabled_by_default(self):
        """Capture disabled by default."""
        with patch.dict(os.environ, {'AIMMS_SINGLE_SITE_POLICY_KEY': 'x'}, clear=False):
            os.environ.pop('AIMMS_VOICE_CAPTURE_ENABLED', None)
            response = self._create(self._client())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'CAPTURE_PURPOSE_UNSUPPORTED')

    def test_full_review_flow_over_http(self):
        """Full review flow over http."""
        with patch.dict(os.environ, self._env(), clear=False):
            client = self._client()
            created = self._create(client)
            self.assertEqual(created.status_code, 201, created.content)
            capture_id = created.json()['id']
            self.assertEqual(created.json()['consent_version'], 'consent-v1')

            revised = client.post(
                f'/api/voice/captures/{capture_id}/revise/',
                {'full_text': 'Seal is leaking at 5 psi.'},
                content_type='application/json',
            )
            self.assertEqual(revised.status_code, 200, revised.content)
            revision = revised.json()['revisions'][0]
            self.assertEqual(revised.json()['state'], 'review')

            accepted = client.post(
                f'/api/voice/captures/{capture_id}/accept/',
                {
                    'revision_id': revision['id'],
                    'content_hash': revision['content_hash'],
                },
                content_type='application/json',
            )
            self.assertEqual(accepted.status_code, 200, accepted.content)
            self.assertEqual(accepted.json()['state'], 'accepted')

            committed = client.post(
                f'/api/voice/captures/{capture_id}/commit/'
            )
            self.assertEqual(committed.status_code, 503)
            self.assertEqual(
                committed.json()['error'], 'DESTINATION_UNAVAILABLE'
            )

    def test_hash_mismatch_is_rejected_over_http(self):
        """Hash mismatch is rejected over http."""
        with patch.dict(os.environ, self._env(), clear=False):
            client = self._client()
            capture_id = self._create(client).json()['id']
            revision = client.post(
                f'/api/voice/captures/{capture_id}/revise/',
                {'full_text': 'Exact words.'},
                content_type='application/json',
            ).json()['revisions'][0]
            response = client.post(
                f'/api/voice/captures/{capture_id}/accept/',
                {
                    'revision_id': revision['id'],
                    'content_hash': hashlib.sha256(b'other').hexdigest(),
                },
                content_type='application/json',
            )
            self.assertEqual(response.status_code, 409)

    def test_closeout_purpose_stays_absent(self):
        """Closeout purpose stays absent."""
        with patch.dict(os.environ, self._env(), clear=False):
            response = self._create(self._client(), purpose='closeout')
        self.assertEqual(response.status_code, 403)

    def test_cross_owner_is_indistinguishable_from_missing(self):
        """Cross owner is indistinguishable from missing."""
        with patch.dict(os.environ, self._env(), clear=False):
            client = self._client()
            capture_id = self._create(client).json()['id']
            stranger = get_user_model().objects.create_user(
                username='cap-api-stranger', password='pw'
            )
            self.client.force_login(stranger)
            response = self.client.get(f'/api/voice/captures/{capture_id}/')
            self.assertEqual(response.status_code, 404)

    def test_scope_change_hides_existing_capture(self):
        """Scope change hides existing capture."""
        with patch.dict(os.environ, self._env(), clear=False):
            client = self._client()
            capture_id = self._create(client).json()['id']

        changed_scope = {**self._env(), 'AIMMS_SINGLE_SITE_POLICY_KEY': 'other-site'}
        with patch.dict(os.environ, changed_scope, clear=False):
            detail = client.get(f'/api/voice/captures/{capture_id}/')
            listing = client.get('/api/voice/captures/')

        self.assertEqual(detail.status_code, 404)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()['results'], [])

    def test_unauthenticated_is_rejected(self):
        """Unauthenticated is rejected."""
        response = self.client.get('/api/voice/captures/')
        self.assertIn(response.status_code, (401, 403))

    def test_api_token_cannot_create_capture(self):
        """Api token cannot create capture."""
        token = ApiToken.objects.create(user=self.tech, name='capture-api-token')
        with patch.dict(os.environ, self._env(), clear=False):
            response = self.client.post(
                '/api/voice/captures/',
                {
                    'purpose': 'fault_intake',
                    'work_order_id': self.work_order.pk,
                    'work_order_version': self.work_order.lifecycle_version,
                },
                content_type='application/json',
                HTTP_AUTHORIZATION=f'Token {token.key}',
            )

        self.assertIn(response.status_code, (401, 403))
        self.assertFalse(VoiceCaptureSession.objects.filter(owner=self.tech).exists())

    def test_non_pilot_user_cannot_create_capture(self):
        """Non pilot user cannot create capture."""
        outsider = get_user_model().objects.create_user(
            username='cap-api-outsider', password='pw'
        )
        self.client.force_login(outsider)

        with patch.dict(os.environ, self._env(), clear=False):
            response = self._create(self.client)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'CAPTURE_PURPOSE_UNSUPPORTED')

    def test_capture_target_must_exist_at_expected_version(self):
        """Capture target must exist at expected version."""
        with patch.dict(os.environ, self._env(), clear=False):
            client = self._client()
            missing = client.post(
                '/api/voice/captures/',
                {
                    'purpose': 'fault_intake',
                    'work_order_id': 2_147_483_647,
                    'work_order_version': 1,
                },
                content_type='application/json',
            )
            stale = client.post(
                '/api/voice/captures/',
                {
                    'purpose': 'fault_intake',
                    'work_order_id': self.work_order.pk,
                    'work_order_version': self.work_order.lifecycle_version + 1,
                },
                content_type='application/json',
            )

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()['error'], 'CAPTURE_TARGET_INVALID')
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()['error'], 'DESTINATION_STALE')
        self.assertFalse(VoiceCaptureSession.objects.filter(owner=self.tech).exists())
