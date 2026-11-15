"""Current-request authorized placement metadata, separate from native authority."""

from django.test import TransactionTestCase, override_settings

from rest_framework.test import APIRequestFactory, force_authenticate

from assets.location_api import LocationMachines
from assets.tests.integration_s5_test_support import NativePlacementFixture


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class PlacementMetadataTests(NativePlacementFixture, TransactionTestCase):
    """Metadata never enrolls a reader or replaces current native source access."""

    def setUp(self):
        """Prepare only a synthetic source/projection, not a default user opt-in."""
        super().setUp()
        from integration_state.services import (
            activate_namespace,
            bootstrap_projection,
            prepare_projection,
        )

        self.namespace = activate_namespace('synthetic-s5')
        self.enrollment = prepare_projection(
            self.namespace.pk, 'synthetic-projection', self.owner.pk
        )
        bootstrap_projection(self.enrollment.pk)

    def request(self):
        """Use the actual DRF request intake rather than a guessed actor wrapper."""
        raw = APIRequestFactory().get('/api/assets/locations/machines/')
        force_authenticate(raw, user=self.actor)
        return LocationMachines().initialize_request(raw)

    def serving(self):
        """Stage independent synthetic placement, not a copied service expectation."""
        from assets.integration_contracts import (
            ClientSourceIdentity,
            MachineSourceIdentity,
        )
        from assets.integration_s5_contracts import (
            PlacementExpectation,
            ProjectionBinding,
        )
        from integration_state.tests.test_acknowledgements import (
            SyntheticServingAdapter,
        )

        return SyntheticServingAdapter(
            ProjectionBinding(
                environment='synthetic-s5',
                namespace_id=self.namespace.pk,
                namespace_epoch=self.namespace.epoch,
                enrollment_id=self.enrollment.pk,
                configuration_epoch=self.enrollment.epoch,
                projection='synthetic-projection',
                client=ClientSourceIdentity('synthetic-s5', self.owner.pk),
            ),
            PlacementExpectation(
                machine=MachineSourceIdentity('synthetic-s5', self.machine.pk),
                client=ClientSourceIdentity('synthetic-s5', self.owner.pk),
                location=None,
                native_version=0,
                fence=1,
                namespace_epoch=self.namespace.epoch,
            ),
        )

    def test_authorized_enrolled_metadata_is_pending_without_a_serving_receipt(self):
        """A committed baseline without serving proof is pending, not current."""
        from assets.integration_s5_pending import (
            PlacementReadAdapter,
            metadata_for_machine,
        )

        policy = PlacementReadAdapter(
            actor_pk=self.actor.pk,
            namespace_id=self.namespace.pk,
            projection='synthetic-projection',
            authorize_metadata=lambda request, binding, placement: True,
            serving=None,
        )
        self.assertEqual(
            metadata_for_machine(self.request(), self.machine.pk, adapter=policy),
            {
                'schema_version': 'aimms.placement.sync.v1',
                'state': 'pending',
                'machine': str(self.machine.pk),
                'client': str(self.owner.pk),
                'location': None,
                'native_version': '0',
                'source_fence': '1',
            },
        )

    def test_current_requires_fresh_serving_proof_matching_the_accepted_generation(
        self,
    ):
        """An old accepted receipt is insufficient when actually serving changes."""
        from assets.integration_s5_ack import acknowledge_latest
        from assets.integration_s5_pending import (
            PlacementReadAdapter,
            metadata_for_machine,
        )

        serving = self.serving()
        acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=serving)
        policy = PlacementReadAdapter(
            actor_pk=self.actor.pk,
            namespace_id=self.namespace.pk,
            projection='synthetic-projection',
            authorize_metadata=lambda request, binding, placement: True,
            serving=serving,
        )
        self.assertEqual(
            metadata_for_machine(self.request(), self.machine.pk, adapter=policy)[
                'state'
            ],
            'current',
        )
        serving.generation = 'different-actually-serving-generation'
        self.assertNotEqual(
            metadata_for_machine(self.request(), self.machine.pk, adapter=policy)[
                'state'
            ],
            'current',
        )

    def test_revocation_during_source_aba_cannot_escape_via_unqualified_return(self):
        """A source-mismatch branch must recheck independent request/grant authority."""
        from assets.integration_s5_ack import acknowledge_latest
        from assets.integration_s5_pending import (
            PlacementReadAdapter,
            metadata_for_machine,
        )
        from assets.locations import client_ids
        from assets.models import AssetMachine, ClientScopeGrant

        serving = self.serving()
        acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=serving)
        policy = PlacementReadAdapter(
            actor_pk=self.actor.pk,
            namespace_id=self.namespace.pk,
            projection='synthetic-projection',
            authorize_metadata=lambda request, binding, placement: True,
            serving=serving,
        )
        verify = serving.verify_observation

        def revoke(observation):
            valid = verify(observation)
            AssetMachine.objects.filter(pk=self.machine.pk).update(
                physical_location=self.destination
            )
            AssetMachine.objects.filter(pk=self.machine.pk).update(
                physical_location=None
            )
            ClientScopeGrant.objects.filter(user=self.actor, client=self.owner).delete()
            self.assertIn(self.owner.pk, client_ids(self.actor))
            return valid

        serving.verify_observation = revoke
        with override_settings(
            AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: [
                {'client_id': self.owner.pk}
            ]
        ):
            self.assertIsNone(
                metadata_for_machine(self.request(), self.machine.pk, adapter=policy)
            )
            self.assertIn(self.owner.pk, client_ids(self.actor))

    def test_metadata_refuses_unqualified_router_before_actor_or_source_orm(self):
        """Routing admission precedes even the current-actor database lookup."""
        from assets.integration_s5_pending import (
            PlacementReadAdapter,
            metadata_for_machine,
        )
        from integration_state.services import PlacementStateError

        class UnqualifiedRouter:
            """A real Django router object that must not be called."""

            def db_for_read(self, *args, **kwargs):
                """Expose a read that occurred before integration admission."""
                raise AssertionError('Unqualified metadata routing was invoked')

            db_for_write = db_for_read

        policy = PlacementReadAdapter(
            actor_pk=self.actor.pk,
            namespace_id=self.namespace.pk,
            projection='synthetic-projection',
            authorize_metadata=lambda request, binding, placement: True,
            serving=None,
        )
        request = self.request()
        with override_settings(DATABASE_ROUTERS=[UnqualifiedRouter()]):
            with self.assertRaises(PlacementStateError):
                metadata_for_machine(request, self.machine.pk, adapter=policy)

    def test_actual_native_get_adds_only_opted_in_authorized_placement_metadata(self):
        """Real permission dispatch and native page fields survive optional wiring."""
        from unittest.mock import patch

        from rest_framework.test import APIClient

        from assets.integration_s5_pending import PlacementReadAdapter

        api = APIClient()
        api.force_authenticate(user=self.actor)
        url = '/api/assets/locations/machines/'
        with override_settings(AIMMS_PLACEMENT_STATE_ADAPTER=None):
            with patch(
                'assets.integration_s5_pending.metadata_for_machine',
                side_effect=AssertionError('Default-off metadata execution'),
            ):
                baseline = api.get(
                    url,
                    {'machine': self.machine.pk},
                    HTTP_X_AIMMS_PLACEMENT_PROJECTION='caller-is-not-enrollment',
                )
        self.assertEqual(baseline.status_code, 200)
        original = baseline.json()
        self.assertNotIn('placement_sync', original['results'][0])
        policy = PlacementReadAdapter(
            actor_pk=self.actor.pk,
            namespace_id=self.namespace.pk,
            projection='synthetic-projection',
            authorize_metadata=lambda request, binding, placement: True,
            serving=None,
        )
        with override_settings(AIMMS_PLACEMENT_STATE_ADAPTER=lambda request: policy):
            response = api.get(url, {'machine': self.machine.pk})
        self.assertEqual(response.status_code, 200)
        current = response.json()
        metadata = current['results'][0].pop('placement_sync', None)
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata['state'], 'pending')
        self.assertEqual(metadata['source_fence'], '1')
        self.assertEqual(current, original)
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_actor_revoked_inside_final_renderer_cannot_receive_native_or_metadata_bytes(
        self,
    ):
        """Actual HTTP rendering must not outlive current request authority."""
        from unittest.mock import patch

        from django.contrib.auth import get_user_model

        from rest_framework.renderers import JSONRenderer
        from rest_framework.test import APIClient

        from assets.integration_s5_pending import PlacementReadAdapter

        policy = PlacementReadAdapter(
            actor_pk=self.actor.pk,
            namespace_id=self.namespace.pk,
            projection='synthetic-projection',
            authorize_metadata=lambda request, binding, placement: True,
            serving=None,
        )
        render = JSONRenderer.render

        def revoke(renderer, data, *args, **kwargs):
            output = render(renderer, data, *args, **kwargs)
            if isinstance(data, dict) and 'results' in data:
                get_user_model().objects.filter(pk=self.actor.pk).update(
                    is_active=False
                )
            return output

        api = APIClient()
        api.force_authenticate(user=self.actor)
        with override_settings(AIMMS_PLACEMENT_STATE_ADAPTER=lambda request: policy):
            with patch.object(JSONRenderer, 'render', revoke):
                response = api.get(
                    '/api/assets/locations/machines/', {'machine': self.machine.pk}
                )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(self.machine.name.encode(), response.content)
        self.assertNotIn('results', response.json())

    def test_renderer_error_rechecks_authority_before_emitting_a_failure(self):
        """Denied authority takes precedence over a fallible encoder's old context."""
        from unittest.mock import patch

        from django.contrib.auth import get_user_model

        from rest_framework.renderers import JSONRenderer
        from rest_framework.test import APIClient

        from assets.integration_s5_pending import PlacementReadAdapter

        policy = PlacementReadAdapter(
            actor_pk=self.actor.pk,
            namespace_id=self.namespace.pk,
            projection='synthetic-projection',
            authorize_metadata=lambda request, binding, placement: True,
            serving=None,
        )

        def revoke_and_fail(renderer, data, *args, **kwargs):
            get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
            raise RuntimeError('Synthetic final encoder failure')

        api = APIClient(raise_request_exception=False)
        api.force_authenticate(user=self.actor)
        with override_settings(AIMMS_PLACEMENT_STATE_ADAPTER=lambda request: policy):
            with patch.object(JSONRenderer, 'render', revoke_and_fail):
                response = api.get(
                    '/api/assets/locations/machines/', {'machine': self.machine.pk}
                )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(self.machine.name.encode(), response.content)

    def test_scope_narrowing_cannot_leak_old_count_when_displayed_row_stays_authorized(
        self,
    ):
        """Page-row checks alone do not protect off-page authorized totals."""
        from unittest.mock import patch

        from rest_framework.renderers import JSONRenderer
        from rest_framework.test import APIClient

        from assets.integration_s5_pending import PlacementReadAdapter
        from assets.models import AssetMachine, Client, ClientScopeGrant

        other = Client.objects.create(name='Synthetic count owner', code='s5-count')
        AssetMachine.objects.create(name='ZZ Synthetic off-page', client=other)
        ClientScopeGrant.objects.create(user=self.actor, client=other)
        policy = PlacementReadAdapter(
            actor_pk=self.actor.pk,
            namespace_id=self.namespace.pk,
            projection='synthetic-projection',
            authorize_metadata=lambda request, binding, placement: True,
            serving=None,
        )
        render = JSONRenderer.render

        def narrow(renderer, data, *args, **kwargs):
            output = render(renderer, data, *args, **kwargs)
            if isinstance(data, dict) and 'results' in data:
                self.assertEqual(data['count'], 2)
                self.assertEqual(data['results'][0]['pk'], self.machine.pk)
                ClientScopeGrant.objects.filter(user=self.actor, client=other).delete()
            return output

        api = APIClient()
        api.force_authenticate(user=self.actor)
        with override_settings(AIMMS_PLACEMENT_STATE_ADAPTER=lambda request: policy):
            with patch.object(JSONRenderer, 'render', narrow):
                response = api.get('/api/assets/locations/machines/', {'limit': 1})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('count', response.json())

    def test_placement_capability_selects_native_before_any_optional_read(self):
        """Combined provider/placement capability is unqualified, not post-fetch fallback."""
        from unittest.mock import patch

        from rest_framework.test import APIClient

        from assets.integration_s2_native import serve_native_read
        from assets.integration_s5_pending import PlacementReadAdapter

        api = APIClient()
        api.force_authenticate(user=self.actor)
        policy = PlacementReadAdapter(
            actor_pk=self.actor.pk,
            namespace_id=self.namespace.pk,
            projection='synthetic-projection',
            authorize_metadata=lambda request, binding, placement: True,
            serving=None,
        )
        with override_settings(AIMMS_PLACEMENT_STATE_ADAPTER=lambda request: policy):
            with patch(
                'assets.integration_s2_native.serve_native_read',
                wraps=serve_native_read,
            ) as optional:
                response = api.get(
                    '/api/assets/locations/machines/', {'machine': self.machine.pk}
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()['results'][0]['placement_sync']['state'], 'pending'
        )
        self.assertEqual(optional.call_count, 0)
        with override_settings(AIMMS_PLACEMENT_STATE_ADAPTER=lambda request: None):
            with patch(
                'assets.integration_s2_native.serve_native_read',
                wraps=serve_native_read,
            ) as optional:
                response = api.get(
                    '/api/assets/locations/machines/', {'machine': self.machine.pk}
                )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('placement_sync', response.json()['results'][0])
        self.assertEqual(optional.call_count, 1)

    def test_metadata_store_failure_is_typed_and_rechecks_revoked_authority(self):
        """A reached read-store failure is neither a native retry nor leaked old rows."""
        from unittest.mock import patch

        from django.contrib.auth import get_user_model

        from rest_framework.test import APIClient

        from assets.integration_s5_pending import PlacementReadAdapter
        from integration_state.services import PlacementStoreUnavailableError

        policy = PlacementReadAdapter(
            actor_pk=self.actor.pk,
            namespace_id=self.namespace.pk,
            projection='synthetic-projection',
            authorize_metadata=lambda request, binding, placement: True,
            serving=None,
        )
        api = APIClient(raise_request_exception=False)
        api.force_authenticate(user=self.actor)
        native = LocationMachines._get_native
        for revoke in (False, True):
            with self.subTest(revoke=revoke):
                calls = []

                def observe(view, request, observations=calls):
                    observations.append('native')
                    return native(view, request)

                def fail(*args, revoked=revoke, **kwargs):
                    if revoked:
                        get_user_model().objects.filter(pk=self.actor.pk).update(
                            is_active=False
                        )
                    raise PlacementStoreUnavailableError(
                        'Synthetic private store failure'
                    )

                try:
                    with override_settings(
                        AIMMS_PLACEMENT_STATE_ADAPTER=lambda request: policy
                    ):
                        with patch.object(LocationMachines, '_get_native', observe):
                            with patch(
                                'assets.integration_s5_pending.metadata_for_machine',
                                fail,
                            ):
                                response = api.get(
                                    '/api/assets/locations/machines/',
                                    {'machine': self.machine.pk},
                                )
                finally:
                    get_user_model().objects.filter(pk=self.actor.pk).update(
                        is_active=True
                    )
                self.assertEqual(response.status_code, 403 if revoke else 503)
                self.assertEqual(calls, ['native'])
                self.assertNotIn(self.machine.name.encode(), response.content)
                self.assertNotIn(b'Synthetic private store failure', response.content)
                if not revoke:
                    self.assertEqual(response.json()['code'], 'unavailable')
