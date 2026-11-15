"""Current-authority composition through actual asset GETs with fake transport.

Request/role/native-scope/grant/source-identity queries use real synthetic ORM
rows. Field-policy and source-generation proofs remain explicitly simulated.
"""

from dataclasses import replace
from datetime import timedelta
from unittest import mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIClient
from tasks.scope import MaintenanceScope

from assets.models import AssetLocation, AssetMachine, Client, ClientScopeGrant
from assets.tests.test_integration_s2_native_parity_db import BASE, S2SourceFixture
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_ASSET_READ_ADAPTER=None,
)
class NativePageAuthorityTests(S2SourceFixture, TestCase):
    """A provider claim cannot grant current local row or emission authority."""

    def setUp(self):
        """Seed only run-owned users, grants and source records."""
        self.build_source()
        self.url = f'{BASE}machines/'
        self.oracle = self.api.get(self.url, {'limit': 100}).json()['results']

    def read_optional(self, adapter, *, params=None):
        """Record the native fallback separately from alternative execution."""
        from assets.location_api import LocationMachines

        original = LocationMachines._get_native
        with (
            self.settings(AIMMS_ASSET_READ_ADAPTER=adapter),
            mock.patch.object(
                LocationMachines, '_get_native', autospec=True, side_effect=original
            ) as native,
        ):
            response = self.api.get(self.url, params or {'limit': 1})
        return response, native.call_count

    def test_native_scope_can_narrow_a_larger_explicit_grant_set(self):
        """Extra grants cannot force native fallback or widen a narrowed shape."""
        own_scope = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.owner.pk)
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: own_scope):
            native = self.api.get(self.url, {'limit': 100}).json()
            self.assertNotIn(self.other.pk, {row['pk'] for row in native['results']})
            self.assertTrue(
                ClientScopeGrant.objects.filter(
                    user=self.reader, client=self.second_owner
                ).exists()
            )
            adapter, sources = self.simulated_adapter(native['results'])
            result, fallback_count = self.read_optional(adapter)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['count'], native['count'])
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].binding.client_ids, (self.owner.pk,))
        self.assertEqual(fallback_count, 0)

    def test_explicit_grant_is_required_with_independently_positive_native_scope(self):
        """The native UI fallback is not mislabeled a strict integration result."""
        own_scope = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.owner.pk)
        }
        ClientScopeGrant.objects.filter(user=self.reader).delete()
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: own_scope):
            native = self.api.get(self.url, {'limit': 1})
            self.assertEqual(native.status_code, 200)
            self.assertGreater(native.data['count'], 0)
            adapter, sources = self.simulated_adapter(self.oracle)
            result, fallback_count = self.read_optional(adapter)
        self.assertEqual(result.json(), native.json())
        self.assertEqual(sources, [])
        self.assertEqual(fallback_count, 1)

    def test_native_scope_is_required_while_matching_grants_remain(self):
        """A grant alone cannot authorize the configured alternative."""
        site_scope = {
            MaintenanceScope(
                customer_id=None, site_key='restricted-site', client_id=self.owner.pk
            )
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: site_scope):
            self.assertTrue(
                ClientScopeGrant.objects.filter(
                    user=self.reader, client=self.owner
                ).exists()
            )
            adapter, sources = self.simulated_adapter(self.oracle)
            result, fallback_count = self.read_optional(adapter)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(sources, [])
        self.assertEqual(fallback_count, 1)

    def test_current_grant_revocation_during_fetch_is_denied_not_empty_or_native(self):
        """A useful initial positive cannot retain a revoked client in its count."""

        def revoke(page):
            ClientScopeGrant.objects.filter(
                user=self.reader, client=self.owner
            ).delete()
            return page

        adapter, sources = self.simulated_adapter(self.oracle, transform=revoke)
        response, native = self.read_optional(adapter)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'denied')
        self.assertNotIn('count', response.data)
        self.assertNotIn('results', response.data)
        self.assertEqual(len(sources[0].calls), 1)
        self.assertEqual(native, 0)

    def test_current_account_role_and_client_loss_during_fetch_are_denied(self):
        """One authority changes at a time while the other prerequisites remain."""
        mutations = (
            lambda: (
                get_user_model()
                .objects.filter(pk=self.reader.pk)
                .update(is_active=False)
            ),
            lambda: RuleSet.objects.filter(
                group=self.reader_group, name='work_order'
            ).update(can_view=False),
            lambda: Client.objects.filter(pk=self.owner.pk).update(active=False),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation.__name__):

                def revoke(page, mutation=mutation):
                    mutation()
                    return page

                adapter, sources = self.simulated_adapter(self.oracle, transform=revoke)
                response, native = self.read_optional(adapter)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.data['code'], 'denied')
                self.assertEqual(len(sources[0].calls), 1)
                self.assertEqual(native, 0)
                get_user_model().objects.filter(pk=self.reader.pk).update(
                    is_active=True
                )
                RuleSet.objects.filter(
                    group=self.reader_group, name='work_order'
                ).update(can_view=True)
                Client.objects.filter(pk=self.owner.pk).update(active=True)
        adapter, _ = self.simulated_adapter(self.oracle)
        self.assertEqual(self.read_optional(adapter)[0].status_code, 200)

    def test_transferred_out_source_row_is_denied_even_when_global_grants_match(self):
        """Current source ownership is checked separately from request metadata."""

        def transfer(page):
            AssetMachine.objects.filter(pk=self.direct.pk).update(
                client_id=self.foreign.pk
            )
            return page

        adapter, sources = self.simulated_adapter(self.oracle, transform=transfer)
        response, native = self.read_optional(adapter)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'denied')
        self.assertEqual(len(sources[0].calls), 1)
        self.assertEqual(native, 0)

    def test_forged_foreign_row_is_not_authorized_by_its_own_source_proof(self):
        """A lying fake proof cannot waive real current source identity scope."""
        forged = {
            **self.oracle[0],
            'pk': self.hidden.pk,
            'name': 'Synthetic forbidden',
            'client': self.foreign.pk,
        }
        adapter, _ = self.simulated_adapter([forged])
        response, native = self.read_optional(adapter)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'denied')
        self.assertNotIn('Synthetic forbidden', str(response.data))
        self.assertEqual(native, 0)

    def test_independent_field_refusal_selects_native_before_provider_work(self):
        """No alternative count/search/enrichment read follows refused shape policy."""
        adapter, sources = self.simulated_adapter(
            self.oracle, fields_allowed=lambda request, binding: False
        )
        response, native = self.read_optional(adapter)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sources, [])
        self.assertEqual(native, 1)

    def test_page_refusal_and_final_emission_revocation_do_not_fallback(self):
        """The independent page-policy seam and last fresh recheck both execute."""
        adapter, sources = self.simulated_adapter(
            self.oracle, page_allowed=lambda request, binding, page: False
        )
        response, native = self.read_optional(adapter)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(sources[0].calls), 1)
        self.assertEqual(native, 0)

        def revoke_before_emission(request, binding, page):
            ClientScopeGrant.objects.filter(
                user=self.reader, client=self.owner
            ).delete()
            return True

        adapter, sources = self.simulated_adapter(
            self.oracle, page_allowed=revoke_before_emission
        )
        response, native = self.read_optional(adapter)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'denied')
        self.assertEqual(len(sources[0].calls), 1)
        self.assertEqual(native, 0)

    def test_unsupported_shapes_and_capabilities_choose_native_before_data_execution(
        self,
    ):
        """Caller-selected providers/actors, unknown order and invalid flags are not trusted."""
        for params in (
            {'provider': 'external'},
            {'actor_pk': '999'},
            {'ordering': '-name'},
            {'active': 'invalid'},
            {'location': self.site.pk, 'unassigned': 'true'},
        ):
            with self.subTest(params=params):
                expected = self.api.get(self.url, params)
                adapter, sources = self.simulated_adapter(self.oracle)
                result, fallback_count = self.read_optional(adapter, params=params)
                self.assertEqual(result.status_code, expected.status_code)
                self.assertEqual(result.json(), expected.json())
                self.assertEqual(sources, [])
                self.assertEqual(fallback_count, 1)
        adapter, sources = self.simulated_adapter(self.oracle)
        factory = adapter.plan_for
        adapter = replace(
            adapter,
            plan_for=lambda binding: replace(
                factory(binding),
                capability=replace(factory(binding).capability, generation=''),
            ),
        )
        response, native = self.read_optional(adapter)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(not source.calls for source in sources))
        self.assertEqual(native, 1)

    def test_selected_provider_faults_remain_sanitized_http_failures(self):
        """No timeout, missing proof or provider exception produces empty HTTP 200."""
        for transform, code in (
            (lambda page: replace(page, coverage_complete=False), 'incomplete'),
            (lambda page: replace(page, total=0, rows=()), 'incomplete'),
        ):
            with self.subTest(code=code):
                adapter, sources = self.simulated_adapter(
                    self.oracle, transform=transform
                )
                response, native = self.read_optional(adapter)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.data['code'], code)
                self.assertEqual(len(sources[0].calls), 1)
                self.assertEqual(native, 0)
        for fault in (
            TimeoutError('private-sentinel'),
            RuntimeError('private-sentinel'),
        ):

            def fail(page, fault=fault):
                raise fault

            adapter, _ = self.simulated_adapter(self.oracle, transform=fail)
            response, native = self.read_optional(adapter)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('private-sentinel', str(response.data))
            self.assertEqual(native, 0)

    def test_optional_policy_and_plan_timeouts_keep_public_classification(self):
        """The HTTP adapter preserves typed faults at every trusted seam."""
        for stage in ('authorize_fields', 'plan_for', 'authorize_page'):
            for fault, code in (
                (TimeoutError('private-policy-sentinel'), 'timeout'),
                (RuntimeError('private-policy-sentinel'), 'unavailable'),
            ):

                def fail(*args, fault=fault, **kwargs):
                    raise fault

                with self.subTest(stage=stage, code=code):
                    adapter, _ = self.simulated_adapter(self.oracle)
                    response, native = self.read_optional(
                        replace(adapter, **{stage: fail})
                    )
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.data['code'], code)
                    self.assertEqual(set(response.data), {'code', 'detail'})
                    self.assertNotIn('private-policy-sentinel', str(response.data))
                    self.assertEqual(native, 0)
        adapter, _ = self.simulated_adapter(self.oracle)
        self.assertEqual(self.read_optional(adapter)[0].status_code, 200)

    def test_page_policy_transfer_is_rechecked_before_emission(self):
        """A dependent policy read cannot leave transferred source rows visible."""

        def transfer_before_emission(request, binding, page):
            AssetMachine.objects.filter(pk=self.direct.pk).update(
                client_id=self.foreign.pk
            )
            return True

        adapter, sources = self.simulated_adapter(
            self.oracle, page_allowed=transfer_before_emission
        )
        response, native = self.read_optional(adapter)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'denied')
        self.assertNotIn('results', response.data)
        self.assertNotIn('count', response.data)
        self.assertEqual(len(sources[0].calls), 1)
        self.assertEqual(native, 0)
        AssetMachine.objects.filter(pk=self.direct.pk).update(client_id=self.owner.pk)
        allowed, _ = self.simulated_adapter(self.oracle)
        self.assertEqual(self.read_optional(allowed)[0].status_code, 200)

    def test_response_construction_cannot_outlive_current_row_or_grant_authority(self):
        """Recheck after the actual response builder, not just before it."""
        from assets.location_api import LocationPagination

        original = LocationPagination.get_paginated_response
        for change in ('grant', 'row'):
            with self.subTest(change=change):

                def build(pagination, rows, change=change):
                    response = original(pagination, rows)
                    if change == 'grant':
                        ClientScopeGrant.objects.filter(
                            user=self.reader, client=self.owner
                        ).delete()
                    else:
                        AssetMachine.objects.filter(pk=self.direct.pk).update(
                            client_id=self.foreign.pk
                        )
                    return response

                adapter, sources = self.simulated_adapter(self.oracle)
                with mock.patch.object(
                    LocationPagination,
                    'get_paginated_response',
                    autospec=True,
                    side_effect=build,
                ) as constructor:
                    response, native = self.read_optional(adapter)
                # Restore before assertions so a failed subtest cannot mask
                # the next interleaving with authority still revoked.
                ClientScopeGrant.objects.get_or_create(
                    user=self.reader, client=self.owner
                )
                AssetMachine.objects.filter(pk=self.direct.pk).update(
                    client_id=self.owner.pk
                )
                allowed, _ = self.simulated_adapter(self.oracle)
                self.assertEqual(self.read_optional(allowed)[0].status_code, 200)
                self.assertEqual(constructor.call_count, 1)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.data['code'], 'denied')
                self.assertNotIn('results', response.data)
                self.assertEqual(len(sources[0].calls), 1)
                self.assertEqual(native, 0)

    def test_superuser_without_matching_grants_does_not_reach_optional_source(self):
        """Superuser authority is not an integration grant."""
        actor = get_user_model().objects.create_user(
            username='s2-super', is_superuser=True, is_staff=True
        )
        self.api = APIClient()
        self.api.force_authenticate(actor)
        adapter, sources = self.simulated_adapter(self.oracle)
        _, native = self.read_optional(adapter)
        self.assertEqual(sources, [])
        self.assertEqual(native, 1)

    def oauth_transport(self):
        """Synthetic authentication only, backed by a real owned token row."""
        from oauth2_provider.contrib.rest_framework.authentication import (
            OAuth2Authentication,
        )
        from oauth2_provider.models import get_access_token_model

        from users.oauth2_scopes import get_granular_scope

        actor = self.reader
        token_type = get_access_token_model()
        token = token_type.objects.create(
            user=actor,
            token='s2-synthetic-local-token',
            scope=' '.join((
                get_granular_scope('view', 'work_order'),
                get_granular_scope('view', 'admin'),
            )),
            expires=timezone.now() + timedelta(hours=1),
        )

        class SyntheticOAuth(OAuth2Authentication):
            def authenticate(self, request):
                return actor, token

        return SyntheticOAuth, token

    def test_direct_handler_binds_current_oauth_scope_not_a_native_http_pass(self):
        """S1 request/token composition; deliberately excludes native initial()."""
        from oauth2_provider.models import get_access_token_model
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory

        from assets.location_api import LocationMachines
        from users.oauth2_scopes import get_granular_scope

        authentication, token = self.oauth_transport()
        original = LocationMachines._get_native

        def direct(adapter):
            request = Request(
                APIRequestFactory().get(self.url, {'limit': 1}),
                authenticators=[authentication()],
            )
            with (
                self.settings(AIMMS_ASSET_READ_ADAPTER=adapter),
                mock.patch.object(
                    LocationMachines, '_get_native', autospec=True, side_effect=original
                ) as native,
            ):
                response = LocationMachines().get(request)
            return response, native.call_count

        adapter, sources = self.simulated_adapter(self.oracle)
        response, native = direct(adapter)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(sources), 1)
        self.assertTrue(
            sources[0].binding.request_scope.startswith('native-ui.oauth.v1:')
        )
        self.assertNotIn('s2-synthetic-local-token', sources[0].binding.request_scope)
        self.assertEqual(native, 0)
        token_type = get_access_token_model()
        for narrowed in (get_granular_scope('view', 'work_order'), 'write'):
            with self.subTest(scope=narrowed):
                token_type.objects.filter(pk=token.pk).update(scope=token.scope)

                def revoke_scope(page, narrowed=narrowed):
                    token_type.objects.filter(pk=token.pk).update(scope=narrowed)
                    return page

                adapter, sources = self.simulated_adapter(
                    self.oracle, transform=revoke_scope
                )
                response, native = direct(adapter)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.data['code'], 'denied')
                self.assertEqual(len(sources[0].calls), 1)
                self.assertEqual(native, 0)

    def test_native_oauth_http_gets_match_native_non_oauth_results(self):
        """Synthetic token transport reaches every inherited real GET handler."""
        from assets.location_api import (
            LocationContext,
            LocationDetail,
            LocationList,
            LocationMachines,
            LocationView,
        )

        cases = (
            (LocationContext, f'{BASE}context/'),
            (LocationList, BASE),
            (LocationMachines, self.url),
            (LocationDetail, f'{BASE}{self.site.pk}/'),
        )
        expected = {url: self.api.get(url, {'limit': 1}).json() for _, url in cases}
        authentication, _ = self.oauth_transport()
        self.api = APIClient()
        with mock.patch.object(
            LocationView, 'authentication_classes', [authentication]
        ):
            for view, url in cases:
                with self.subTest(view=view.__name__):
                    original = view.get
                    with mock.patch.object(
                        view, 'get', autospec=True, side_effect=original
                    ) as handler:
                        response = self.api.get(url, {'limit': 1})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), expected[url])
                    self.assertEqual(handler.call_count, 1)
                    self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_oauth_scope_refusal_cannot_fall_through_to_native_user_roles(self):
        """Matching user roles cannot override a narrower OAuth credential."""
        from assets.location_api import (
            LocationContext,
            LocationDetail,
            LocationList,
            LocationMachines,
            LocationTransfer,
            LocationView,
        )
        from users.oauth2_scopes import get_granular_scope
        from users.permissions import check_user_role

        # As in build_source, set fixture flags without invoking the unrelated
        # optional-app permission synchronization omitted by this profile.
        RuleSet.objects.filter(group=self.reader_group, name='work_order').update(
            can_view=True, can_add=True, can_change=True
        )
        self.reader = get_user_model().objects.get(pk=self.reader.pk)
        self.assertTrue(check_user_role(self.reader, 'work_order', 'add'))
        self.assertTrue(check_user_role(self.reader, 'work_order', 'change'))
        authentication, token = self.oauth_transport()
        self.api = APIClient()
        read_scope = get_granular_scope('view', 'work_order')
        wrong_read = get_granular_scope('view', 'part')
        cases = (
            (LocationContext, 'get', f'{BASE}context/', {}, wrong_read),
            (LocationList, 'get', BASE, {}, wrong_read),
            (LocationMachines, 'get', self.url, {}, wrong_read),
            (LocationDetail, 'get', f'{BASE}{self.site.pk}/', {}, wrong_read),
            (
                LocationList,
                'post',
                BASE,
                {
                    'client': self.owner.pk,
                    'parent': self.site.pk,
                    'name': 'OAuth refused create',
                    'code': 'oauth-refused-create',
                    'reason': 'Synthetic refused create',
                },
                read_scope,
            ),
            (
                LocationDetail,
                'patch',
                f'{BASE}{self.site.pk}/',
                {
                    'expected_version': self.site.version,
                    'description': 'Synthetic refused patch',
                    'reason': 'Synthetic refused patch',
                },
                read_scope,
            ),
            (
                LocationTransfer,
                'post',
                f'{BASE}transfers/',
                {
                    'machines': [
                        {
                            'machine_id': self.direct.pk,
                            'expected_placement_version': self.direct.placement_version,
                        }
                    ],
                    'destination_location_id': self.room.pk,
                    'idempotency_key': str(uuid4()),
                    'reason': 'Synthetic refused move',
                },
                read_scope,
            ),
        )
        count = AssetLocation.objects.count()
        with mock.patch.object(
            LocationView, 'authentication_classes', [authentication]
        ):
            for view, method, url, data, scope in cases:
                with self.subTest(view=view.__name__, method=method):
                    token.scope = scope
                    type(token).objects.filter(pk=token.pk).update(scope=scope)
                    original = getattr(view, method)
                    with mock.patch.object(
                        view, method, autospec=True, side_effect=original
                    ) as handler:
                        response = getattr(self.api, method)(url, data, format='json')
                    self.assertEqual(handler.call_count, 0)
                    self.assertEqual(response.status_code, 403)
            token.scope = read_scope
            type(token).objects.filter(pk=token.pk).update(scope=read_scope)
            self.assertEqual(self.api.get(self.url).status_code, 200)
        self.assertEqual(AssetLocation.objects.count(), count)
        self.site.refresh_from_db()
        self.direct.refresh_from_db()
        self.assertEqual(self.site.description, '')
        self.assertEqual(self.direct.physical_location_id, self.site.pk)

    def test_oauth_transfer_uses_change_scope_not_add_scope(self):
        """The transfer POST has change authority, independently of creation."""
        from django.db import transaction

        from assets.location_api import LocationTransfer
        from users.oauth2_scopes import get_granular_scope
        from users.permissions import check_user_role

        RuleSet.objects.filter(group=self.reader_group, name='work_order').update(
            can_change=True, can_add=False
        )
        self.reader = get_user_model().objects.get(pk=self.reader.pk)
        self.assertTrue(check_user_role(self.reader, 'work_order', 'change'))
        self.assertFalse(check_user_role(self.reader, 'work_order', 'add'))
        authentication, token = self.oauth_transport()
        self.api = APIClient()
        original = LocationTransfer.post
        for action, status in (('add', 403), ('change', 200)):
            with self.subTest(action=action):
                token.scope = get_granular_scope(action, 'work_order')
                type(token).objects.filter(pk=token.pk).update(scope=token.scope)
                self.direct.refresh_from_db()
                data = {
                    'machines': [
                        {
                            'machine_id': self.direct.pk,
                            'expected_placement_version': self.direct.placement_version,
                        }
                    ],
                    'destination_location_id': self.room.pk,
                    'idempotency_key': str(uuid4()),
                    'reason': 'Synthetic OAuth scope check',
                }
                # Each actual write is read back, then its test-only savepoint
                # rolls back before the next independent permission case.
                with transaction.atomic():
                    with (
                        mock.patch.object(
                            LocationTransfer, 'authentication_classes', [authentication]
                        ),
                        mock.patch.object(
                            LocationTransfer,
                            'post',
                            autospec=True,
                            side_effect=original,
                        ) as handler,
                    ):
                        response = self.api.post(
                            f'{BASE}transfers/', data, format='json'
                        )
                    self.direct.refresh_from_db()
                    placement = self.direct.physical_location_id
                    transaction.set_rollback(True)
                self.assertEqual(response.status_code, status)
                self.assertEqual(handler.call_count, int(status == 200))
                self.assertEqual(
                    placement, self.room.pk if status == 200 else self.site.pk
                )

    def test_matching_oauth_write_scopes_still_require_native_roles(self):
        """Each write needs both the token scope and native action role."""
        from django.db import transaction

        from assets.location_api import (
            LocationDetail,
            LocationList,
            LocationTransfer,
            LocationView,
        )
        from users.oauth2_scopes import get_granular_scope

        authentication, token = self.oauth_transport()
        self.api = APIClient()
        cases = (
            (
                LocationList,
                'post',
                BASE,
                'add',
                201,
                {
                    'client': self.owner.pk,
                    'parent': self.site.pk,
                    'name': 'OAuth permitted create',
                    'code': 'oauth-permitted-create',
                    'reason': 'Synthetic permitted create',
                },
            ),
            (
                LocationDetail,
                'patch',
                f'{BASE}{self.site.pk}/',
                'change',
                200,
                {
                    'expected_version': self.site.version,
                    'description': 'Synthetic permitted patch',
                    'reason': 'Synthetic permitted patch',
                },
            ),
            (
                LocationTransfer,
                'post',
                f'{BASE}transfers/',
                'change',
                200,
                {
                    'machines': [
                        {
                            'machine_id': self.direct.pk,
                            'expected_placement_version': self.direct.placement_version,
                        }
                    ],
                    'destination_location_id': self.room.pk,
                    'idempotency_key': str(uuid4()),
                    'reason': 'Synthetic permitted transfer',
                },
            ),
        )
        with mock.patch.object(
            LocationView, 'authentication_classes', [authentication]
        ):
            for view, method, url, action, success, data in cases:
                token.scope = get_granular_scope(action, 'work_order')
                type(token).objects.filter(pk=token.pk).update(scope=token.scope)
                self.assertTrue(token.is_valid([token.scope]))
                for allowed in (False, True):
                    with self.subTest(action=action, native_role=allowed):
                        RuleSet.objects.filter(
                            group=self.reader_group, name='work_order'
                        ).update(**{f'can_{action}': allowed})
                        original = getattr(view, method)
                        with transaction.atomic():
                            with mock.patch.object(
                                view, method, autospec=True, side_effect=original
                            ) as handler:
                                response = getattr(self.api, method)(
                                    url, data, format='json'
                                )
                            effect = (
                                AssetLocation.objects.filter(
                                    client=self.owner, code=data['code']
                                ).exists()
                                if view is LocationList
                                else AssetLocation.objects.filter(
                                    pk=self.site.pk, description=data['description']
                                ).exists()
                                if view is LocationDetail
                                else AssetMachine.objects.filter(
                                    pk=self.direct.pk, physical_location=self.room
                                ).exists()
                            )
                            transaction.set_rollback(True)
                        self.assertEqual(
                            response.status_code, success if allowed else 403
                        )
                        self.assertEqual(handler.call_count, int(allowed))
                        self.assertEqual(effect, allowed)

    def test_valid_oauth_scope_cannot_replace_the_native_read_role(self):
        """Independently deny the native role while the credential stays valid."""
        from assets.location_api import LocationMachines

        authentication, token = self.oauth_transport()
        self.api = APIClient()
        self.assertTrue(token.is_valid(token.scope.split()))
        RuleSet.objects.filter(group=self.reader_group, name='work_order').update(
            can_view=False
        )
        adapter, sources = self.simulated_adapter(self.oracle)
        with (
            mock.patch.object(
                LocationMachines, 'authentication_classes', [authentication]
            ),
            mock.patch.object(LocationMachines, 'get') as handler,
        ):
            response, native = self.read_optional(adapter)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(handler.call_count, 0)
        self.assertEqual(sources, [])
        self.assertEqual(native, 0)

    def test_oauth_http_rechecks_stored_scope_expiry_and_token_presence(self):
        """Initially valid HTTP authority cannot survive selected-source work."""
        from assets.location_api import LocationMachines

        authentication, token = self.oauth_transport()
        self.api = APIClient()
        for fault in ('scope', 'expiry', 'deleted'):
            type(token).objects.filter(pk=token.pk).update(
                scope=token.scope, expires=token.expires
            )

            def revoke(page, fault=fault):
                current = type(token).objects.filter(pk=token.pk)
                if fault == 'scope':
                    current.update(scope='write')
                elif fault == 'expiry':
                    current.update(expires=timezone.now() - timedelta(seconds=1))
                else:
                    current.delete()
                return page

            with self.subTest(fault=fault):
                adapter, sources = self.simulated_adapter(self.oracle, transform=revoke)
                with mock.patch.object(
                    LocationMachines, 'authentication_classes', [authentication]
                ):
                    response, native = self.read_optional(adapter)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.data['code'], 'denied')
                self.assertEqual(set(response.data), {'code', 'detail'})
                self.assertEqual(len(sources[0].calls), 1)
                self.assertEqual(native, 0)

    def test_optional_oauth_http_enters_the_current_user_provider(self):
        """Real HTTP permission dispatch, not a direct-handler substitute."""
        from assets.location_api import LocationMachines

        authentication, _ = self.oauth_transport()
        self.api = APIClient()
        adapter, sources = self.simulated_adapter(self.oracle)
        with mock.patch.object(
            LocationMachines, 'authentication_classes', [authentication]
        ):
            response, native = self.read_optional(adapter)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], len(self.oracle))
        self.assertEqual(response.data['results'], self.oracle[:1])
        self.assertEqual(len(sources[0].calls), 1)
        self.assertTrue(
            sources[0].binding.request_scope.startswith('native-ui.oauth.v1:')
        )
        self.assertNotIn('s2-synthetic-local-token', sources[0].binding.request_scope)
        self.assertEqual(native, 0)
