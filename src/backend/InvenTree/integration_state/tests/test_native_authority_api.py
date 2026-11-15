"""Actual native HTTP request/review authentication, CSRF and opaque refusal."""

from integration_state.tests.assertions import assert_literal_bool

from base64 import b64encode

from django.http import HttpRequest
from django.middleware.csrf import get_token
from django.test import TestCase, override_settings

from rest_framework.test import APIClient
from tasks.integration_sources import read_work_order

from assets.models import AssetMachine
from integration_state.models import (
    NativeAuthorityDecision,
    NativeAuthorityRequest,
    NativeAuthorityWithdrawal,
    PrivateDeletionRequest,
)
from integration_state.tests.test_native_authority import NativeAuthorityFixture

BASE = '/api/assets/integration-authority/'
PASSWORD = 'synthetic-source-password'


class NativeAuthorityAPITests(NativeAuthorityFixture, TestCase):
    """Never replace the native authenticator, permission relation or source proof."""

    def setUp(self):
        """Use actual Basic/session authentication with only synthetic accounts."""
        super().setUp()
        self.reviewer.set_password(PASSWORD)
        self.reviewer.save(update_fields=['password'])

    def client_for(self, actor):
        """Construct actual Basic credentials for the selected native account."""
        client = APIClient(enforce_csrf_checks=True)
        credential = b64encode(f'{actor.username}:{PASSWORD}'.encode()).decode()
        client.credentials(HTTP_AUTHORIZATION='Basic ' + credential)
        return client

    def payload(self):
        """Claim only an actual future native Work revision for the granted client."""
        return {
            'purpose': 'attribution',
            'kind': 'work',
            'source_pk': self.new_work.pk,
            'client_ids': [self.client_a.pk],
            'evidence_reference': 'synthetic-http-evidence',
            'request_key': 'synthetic-http-work',
        }

    def test_actual_http_two_actor_attribution_and_retained_withdrawal(self):
        """The real asset URL composition can create/review/withdraw a source claim."""
        owner, reviewer = self.client_for(self.owner), self.client_for(self.reviewer)
        created = owner.post(BASE, self.payload(), format='json')
        self.assertEqual(created.status_code, 201, created.data)
        pk = created.data['request_pk']
        self.assertEqual(
            owner.post(f'{BASE}{pk}/inspect/', {}, format='json').status_code, 404
        )
        view = reviewer.post(f'{BASE}{pk}/inspect/', {}, format='json')
        self.assertEqual(view.status_code, 200, view.data)
        self.assertEqual(
            view.data['request_details']['source'],
            {
                'environment': self.namespace.environment,
                'kind': 'work',
                'source_pk': self.new_work.pk,
            },
        )
        decided = reviewer.post(
            f'{BASE}{pk}/review/',
            {
                'expected_request_digest': view.data['request_digest'],
                'expected_impact_digest': view.data['impact_digest'],
                'approved': True,
            },
            format='json',
        )
        self.assertEqual(decided.status_code, 200, decided.data)
        assert_literal_bool(self, decided.data['erasure_authorized'], False)
        self.assertIsNotNone(
            read_work_order(
                self.owner,
                environment=self.namespace.environment,
                work_pk=self.new_work.pk,
            )
        )
        withdrawn = owner.post(
            f'{BASE}{pk}/withdraw/',
            {
                'expected_request_digest': view.data['request_digest'],
                'reason_reference': 'synthetic-http-correction',
            },
            format='json',
        )
        self.assertEqual(withdrawn.status_code, 200, withdrawn.data)
        self.assertIsNone(
            read_work_order(
                self.owner,
                environment=self.namespace.environment,
                work_pk=self.new_work.pk,
            )
        )
        self.assertEqual(NativeAuthorityRequest.objects.count(), 1)
        self.assertEqual(NativeAuthorityDecision.objects.count(), 1)
        self.assertEqual(NativeAuthorityWithdrawal.objects.count(), 1)

    def test_actual_http_readiness_is_incomplete_and_has_no_erasure_effect(self):
        """A native review signs a bounded impact observation, never removal authority."""
        created = self.client_for(self.owner).post(
            BASE,
            {
                'purpose': 'deletion_readiness',
                'client_pk': self.client_a.pk,
                'client_request_reference': 'synthetic-http-client-request',
                'request_key': 'http-readiness',
                'scope': [
                    {
                        'entity_kind': 'machine',
                        'aspect': 'metadata',
                        'source_id': self.machine.pk,
                    }
                ],
            },
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        pk = created.data['request_pk']
        reviewer = self.client_for(self.reviewer)
        view = reviewer.post(f'{BASE}{pk}/inspect/', {}, format='json')
        self.assertEqual(view.status_code, 200, view.data)
        assert_literal_bool(self, view.data['impact']['coverage_complete'], False)
        self.assertEqual(
            view.data['request_details']['declared_scope'][0]['source_id'],
            self.machine.pk,
        )
        assert_literal_bool(self, view.data['request_details']['source_ownership_verified'], False)
        assert_literal_bool(self, view.data['impact']['erasure_authorized'], False)
        self.assertEqual(view.data['impact']['default_retention'], 'indefinite')
        decided = reviewer.post(
            f'{BASE}{pk}/review/',
            {
                'expected_request_digest': view.data['request_digest'],
                'expected_impact_digest': view.data['impact_digest'],
                'approved': True,
            },
            format='json',
        )
        self.assertEqual(decided.status_code, 200, decided.data)
        assert_literal_bool(self, decided.data['erasure_authorized'], False)
        self.assertEqual(PrivateDeletionRequest.objects.count(), 1)
        self.assertTrue(AssetMachine.objects.filter(pk=self.machine.pk).exists())

    def test_default_off_refuses_before_authentication_or_state_queries(self):
        """Optional URLs cannot enroll a source or require credentials while disabled."""
        with (
            override_settings(AIMMS_INTEGRATION_AUTHORITY_ENABLED=False),
            self.assertNumQueries(0),
        ):
            response = self.client_for(self.owner).post(
                BASE, self.payload(), format='json'
            )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(NativeAuthorityRequest.objects.exists())

    def test_foreign_grants_and_supplied_namespace_cannot_substitute_for_authority(
        self,
    ):
        """Scope and server namespace are independent of a caller's JSON labels."""
        client = self.client_for(self.owner)
        value = self.payload()
        value['client_ids'] = [self.client_b.pk]
        self.assertEqual(client.post(BASE, value, format='json').status_code, 404)
        value = self.payload()
        value['namespace_id'] = self.namespace.pk
        self.assertEqual(client.post(BASE, value, format='json').status_code, 400)
        self.assertFalse(NativeAuthorityRequest.objects.exists())

    def test_get_anonymous_and_unsupported_credentials_make_no_request(self):
        """Read verbs and unrelated bearer-token forms confer no new write permission."""
        self.assertEqual(self.client_for(self.owner).get(BASE).status_code, 405)
        self.assertEqual(
            APIClient().post(BASE, self.payload(), format='json').status_code, 401
        )
        client = APIClient()
        client.credentials(
            HTTP_AUTHORIZATION='Bearer synthetic-not-a-native-write-credential'
        )
        self.assertEqual(
            client.post(BASE, self.payload(), format='json').status_code, 401
        )
        self.assertFalse(NativeAuthorityRequest.objects.exists())

    def test_real_session_requires_native_csrf_and_current_permission(self):
        """Actual session auth cannot post without CSRF, and rechecks action roles."""
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.owner)
        denied = client.post(BASE, self.payload(), format='json')
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(NativeAuthorityRequest.objects.exists())
        request = HttpRequest()
        token = get_token(request)
        client.cookies['csrftoken'] = request.META['CSRF_COOKIE']
        accepted = client.post(
            BASE, self.payload(), format='json', HTTP_X_CSRFTOKEN=token
        )
        self.assertEqual(accepted.status_code, 201, accepted.data)
        self.admin_rule.can_change = False
        self.admin_rule.save()
        value = self.payload()
        value['request_key'] = 'revoked-http-admin'
        denied = client.post(BASE, value, format='json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(NativeAuthorityRequest.objects.count(), 1)
