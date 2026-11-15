"""Real native ORM roles/grants/routes with a synthetic generated SDK worker.

Authentication is DRF forced/synthetic OAuth except the explicit session test.
No real Foundry, real customer, deployment or original C/K acceptance proof.
"""

import os
from dataclasses import replace
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIClient

from assets.models import AssetMachine, ClientScopeGrant
from assets.tests.test_integration_s2_native_parity_db import S2SourceFixture
from integration_state.palantir_transport import TransportError, fetch_object
from integration_state.tests.test_palantir_transport import (
    WorkerFixture,
    fixture_config,
)
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class PalantirPilotTests(WorkerFixture, S2SourceFixture, TestCase):
    """The configured service user cannot replace native end-user authority."""

    def setUp(self):
        """Only owned synthetic native rows are created for this case."""
        self.build_source()
        self.config = fixture_config(
            client=self.owner.pk,
            client_incarnation=self.owner.created_at.isoformat(),
            incarnation=str(self.direct.uuid),
        )
        self.url = f'/api/integration/palantir/machines/{self.direct.pk}/'

    def get(self, *, config=None, url=None):
        """Invoke the actual root API route; no response interception."""
        with override_settings(AIMMS_PALANTIR_TRANSPORT=config or self.config):
            return self.api.get(url or self.url)

    def test_exact_native_authorized_object_uses_actual_worker(self):
        """Return only the reviewed minimal object, never a population claim."""
        with self.use_worker():
            response = self.get()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data,
            {
                'schema_version': 'aimms.palantir.object.v1',
                'source': 'palantir_osdk',
                'consistency': 'single_observation',
                'object': {
                    'pk': self.direct.pk,
                    'client': self.owner.pk,
                    'name': 'Synthetic remote asset',
                },
            },
        )
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_location_uses_exact_birth_timestamp_not_machine_identity(self):
        """Mappings and native authority are domain-specific."""
        config = fixture_config(
            client=self.owner.pk,
            client_incarnation=self.owner.created_at.isoformat(),
            incarnation=self.site.created_at.isoformat(),
        )
        with self.use_worker():
            response = self.get(
                config=config,
                url=f'/api/integration/palantir/locations/{self.site.pk}/',
            )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['object']['pk'], self.site.pk)

    def test_default_off_and_unconfigured_domain_never_fetch_or_resolve(self):
        """Installing routes/config classes does not enroll any native consumer."""
        with mock.patch('integration_state.palantir_api.fetch_object') as fetch:
            with override_settings(AIMMS_PALANTIR_TRANSPORT=None):
                self.assertEqual(self.api.get(self.url).status_code, 404)
            config = replace(self.config, mappings=(self.config.mappings[0],))
            self.assertEqual(
                self.get(
                    config=config,
                    url=f'/api/integration/palantir/locations/{self.site.pk}/',
                ).status_code,
                404,
            )
        fetch.assert_not_called()

    def test_anonymous_requests_cannot_trigger_transport(self):
        """The native authentication gate executes before the pilot handler."""
        with (
            override_settings(AIMMS_PALANTIR_TRANSPORT=self.config),
            mock.patch('integration_state.palantir_api.fetch_object') as fetch,
        ):
            response = APIClient().get(self.url)
        self.assertIn(response.status_code, (401, 403))
        fetch.assert_not_called()

    def test_restore_hold_and_unqualified_database_routers_prevent_dispatch(self):
        """No direct pilot caller can bypass the deployment serving hold."""
        with (
            mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': 'true'}),
            mock.patch('integration_state.palantir_api.fetch_object') as fetch,
        ):
            self.assertEqual(self.get().status_code, 503)
            fetch.assert_not_called()
        with (
            override_settings(DATABASE_ROUTERS=[object()]),
            mock.patch('integration_state.palantir_api.fetch_object') as fetch,
        ):
            self.assertEqual(self.get().status_code, 503)
            fetch.assert_not_called()

    def test_restore_hold_enabled_during_read_withholds_result(self):
        """Recheck the hold before any selected source bytes leave the service."""

        def hold(*args, **kwargs):
            result = fetch_object(*args, **kwargs)
            os.environ['INVENTREE_RESTORE_HOLD'] = 'true'
            return result

        with (
            mock.patch.dict(os.environ),
            self.use_worker(),
            mock.patch('integration_state.palantir_api.fetch_object', side_effect=hold),
        ):
            self.assertEqual(self.get().status_code, 503)

    def test_missing_grant_foreign_row_and_missing_row_do_not_trigger_io(self):
        """Native scope and an explicit active-client grant are both necessary."""
        for pk in (self.hidden.pk, self.direct.pk + 100000):
            with (
                self.subTest(pk=pk),
                mock.patch('integration_state.palantir_api.fetch_object') as fetch,
            ):
                self.assertEqual(
                    self.get(
                        url=f'/api/integration/palantir/machines/{pk}/'
                    ).status_code,
                    403,
                )
                fetch.assert_not_called()
        ClientScopeGrant.objects.filter(user=self.reader, client=self.owner).delete()
        with mock.patch('integration_state.palantir_api.fetch_object') as fetch:
            self.assertEqual(self.get().status_code, 403)
            fetch.assert_not_called()

    def test_wrong_native_scope_does_not_inherit_service_user_scope(self):
        """Even an explicit grant is insufficient without the native scope."""
        with (
            override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda _: []),
            mock.patch('integration_state.palantir_api.fetch_object') as fetch,
        ):
            self.assertEqual(self.get().status_code, 403)
            fetch.assert_not_called()

    def test_inactive_actor_role_and_client_refused_before_secret_access(self):
        """Use fresh database state rather than cached request user permissions."""
        from assets.models import Client

        mutations = [
            lambda: (
                type(self.reader)
                .objects.filter(pk=self.reader.pk)
                .update(is_active=False)
            ),
            lambda: RuleSet.objects.filter(
                group=self.reader_group, name='work_order'
            ).update(can_view=False),
            lambda: Client.objects.filter(pk=self.owner.pk).update(active=False),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.captureOnCommitCallbacks():
                # Use savepoints to independently exercise each refusal.
                from django.db import transaction

                with transaction.atomic():
                    mutate()
                    with mock.patch(
                        'integration_state.palantir_api.fetch_object'
                    ) as fetch:
                        self.assertEqual(self.get().status_code, 403)
                        fetch.assert_not_called()
                    transaction.set_rollback(True)

    def test_unknown_and_truthy_field_policy_results_are_refused(self):
        """No implicit approval of field/relationship mappings."""
        for allowed in (None, False, 0, 1, 'yes'):
            config = replace(
                self.config, authorize_fields=lambda *_, allowed=allowed: allowed
            )
            with (
                self.subTest(allowed=allowed),
                mock.patch('integration_state.palantir_api.fetch_object') as fetch,
            ):
                self.assertEqual(self.get(config=config).status_code, 403)
                fetch.assert_not_called()

    def test_client_grant_revocation_during_sdk_call_withholds_result(self):
        """A successful remote read is not a final authorization decision."""

        def revoke(*args, **kwargs):
            result = fetch_object(*args, **kwargs)
            ClientScopeGrant.objects.filter(
                user=self.reader, client=self.owner
            ).delete()
            return result

        with (
            self.use_worker(),
            mock.patch(
                'integration_state.palantir_api.fetch_object', side_effect=revoke
            ),
        ):
            response = self.get()
        self.assertEqual(response.status_code, 403, response.data)
        self.assertNotIn('object', response.data)

    def test_native_mutation_during_read_withholds_result(self):
        """Observed source changes cannot return a success from old coordinates."""

        def mutate(*args, **kwargs):
            result = fetch_object(*args, **kwargs)
            AssetMachine.objects.filter(pk=self.direct.pk).update(
                name='Changed during read'
            )
            return result

        with (
            self.use_worker(),
            mock.patch(
                'integration_state.palantir_api.fetch_object', side_effect=mutate
            ),
        ):
            self.assertEqual(self.get().status_code, 403)

    def test_configuration_replacement_during_read_withholds_result(self):
        """No return under a replaced environment/credential/policy object."""
        from django.conf import settings

        def replace_config(*args, **kwargs):
            result = fetch_object(*args, **kwargs)
            settings.AIMMS_PALANTIR_TRANSPORT = replace(
                self.config, environment='replaced'
            )
            return result

        with (
            self.use_worker(),
            mock.patch(
                'integration_state.palantir_api.fetch_object',
                side_effect=replace_config,
            ),
        ):
            self.assertEqual(self.get().status_code, 403)

    def test_field_policy_is_checked_again_before_disclosure(self):
        """Revoking field access during the read withholds the selected data."""
        policy = mock.Mock(side_effect=[True, False])
        with self.use_worker():
            self.assertEqual(
                self.get(
                    config=replace(self.config, authorize_fields=policy)
                ).status_code,
                403,
            )
        self.assertEqual(policy.call_count, 2)

    def test_selected_provider_errors_never_fallback_or_disclose_detail(self):
        """Failures cannot become native success, a known zero or an empty list."""
        for code, status in [
            ('unavailable', 503),
            ('timeout', 504),
            ('invalid', 502),
            ('budget', 502),
        ]:
            with (
                self.subTest(code=code),
                mock.patch(
                    'integration_state.palantir_api.fetch_object',
                    side_effect=TransportError(code),
                ),
            ):
                response = self.get()
            self.assertEqual(response.status_code, status)
            self.assertEqual(
                response.data, {'detail': f'Palantir read refused: {code}'}
            )
            self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_remote_identity_mismatch_is_not_disclosed(self):
        """Actual worker data still needs independent local identity checks."""
        with self.use_worker():
            response = self.get(config=fixture_config(client=self.foreign.pk))
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('object', response.data)

    def test_remote_client_pk_reuse_is_withheld(self):
        """Matching integer IDs cannot substitute for the client's lifecycle."""
        config = fixture_config(
            client=self.owner.pk,
            incarnation=str(self.direct.uuid),
            client_incarnation='older-client-birth',
        )
        with self.use_worker():
            response = self.get(config=config)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('object', response.data)

    def test_unknown_domain_and_writes_are_not_supported(self):
        """The pilot exposes no generic query, action or erasure surface."""
        with (
            override_settings(AIMMS_PALANTIR_TRANSPORT=self.config),
            mock.patch('integration_state.palantir_api.fetch_object') as fetch,
        ):
            self.assertEqual(
                self.api.get('/api/integration/palantir/history/1/').status_code, 404
            )
            self.assertEqual(self.api.post(self.url, {}).status_code, 405)
            self.assertEqual(self.api.delete(self.url).status_code, 405)
        fetch.assert_not_called()

    def test_real_session_authentication_reaches_native_policy_and_worker(self):
        """Local synthetic login exercises native authentication transport."""
        self.reader.set_password('synthetic-local-password')
        self.reader.save(update_fields=['password'])
        self.api = APIClient()
        self.assertTrue(
            self.api.login(
                username=self.reader.username, password='synthetic-local-password'
            )
        )
        with self.use_worker():
            response = self.get()
        self.assertEqual(response.status_code, 200, response.data)

    def test_stored_oauth_scope_revocation_during_read_is_rechecked(self):
        """Synthetic OAuth intake with real token expiry/scope rows, not SSO."""
        from oauth2_provider.contrib.rest_framework.authentication import (
            OAuth2Authentication,
        )
        from oauth2_provider.models import get_access_token_model
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory

        from integration_state.palantir_api import read_object
        from users.oauth2_scopes import get_granular_scope

        token_type = get_access_token_model()
        token = token_type.objects.create(
            user=self.reader,
            token='osdk-local-synthetic-token',
            scope=get_granular_scope('view', 'work_order'),
            expires=timezone.now() + timedelta(hours=1),
        )
        actor = self.reader

        class SyntheticOAuth(OAuth2Authentication):
            def authenticate(self, request):
                return actor, token

        request = Request(
            APIRequestFactory().get(self.url), authenticators=[SyntheticOAuth()]
        )

        def revoke(*args, **kwargs):
            result = fetch_object(*args, **kwargs)
            token_type.objects.filter(pk=token.pk).update(scope='write')
            return result

        with (
            override_settings(AIMMS_PALANTIR_TRANSPORT=self.config),
            self.use_worker(),
            mock.patch(
                'integration_state.palantir_api.fetch_object', side_effect=revoke
            ),
            self.assertRaises(TransportError) as raised,
        ):
            read_object(request, domain='machines', pk=self.direct.pk)
        self.assertEqual(raised.exception.code, 'denied')
