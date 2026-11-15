"""Real S1 request/ORM authority composed with source-authorized S6 fixtures."""

from integration_state.tests.assertions import assert_literal_bool

import json
import tempfile
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from assets.activation import point_hash
from assets.health_models import HealthSource, MachineSignalBinding
from assets.models import Client, ClientScopeGrant, DictionaryPoint
from assets.registry import ensure_pump, register_station
from machine_health.integration_contracts import TelemetrySourceIdentity
from machine_health.services.integration_history import HistoryWindow
from machine_health.services.integration_s6_adapter import (
    FixtureHistoryAdapter,
    FixtureMapping,
)
from machine_health.tests.test_integration_s6_wire import observation
from users.models import RuleSet


class S6RequestFixture:
    """Use real local scope/grant/mapping rows and only a fake source transport."""

    def setUp(self):
        """Construct isolated nonempty source truth and a reviewed inactive binding."""
        self.actor = get_user_model().objects.create_user(username='s6-fixture-actor')
        group = Group.objects.create(name='S6 fixture readers')
        self.actor.groups.add(group)
        self.rule, _ = RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.tenant = Client.objects.create(name='S6 fixture', code='s6-fixture')
        self.foreign_tenant = Client.objects.create(name='S6 other', code='s6-other')
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.tenant
        )
        self.station = register_station(
            client=self.tenant,
            name='S6 fixture station',
            source_namespace='synthetic-s6',
            source_entity_uuid=uuid4(),
            source_key='S6_FIXTURE',
        )
        self.machine = ensure_pump(self.station, 'P1')
        self.source = HealthSource.objects.create(
            name='S6 fixture source',
            client=self.tenant,
            source_type='scada',
            connector_type='cosmos_pumphouse',
            config={'stations': [str(self.station.source_entity_uuid)]},
        )
        self.point = DictionaryPoint.objects.create(
            station=self.station,
            machine=self.machine,
            path='/s6/recorded',
            raw_tag='S6_SYNTHETIC',
            display_name='S6 fixture reading',
            data_type='number',
            unit='degC',
            unit_status='verified',
            status='approved',
        )
        self.binding = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=self.source,
            dictionary_point=self.point,
            dictionary_hash=point_hash(self.point),
            external_key=self.point.path,
            display_name='S6 fixture reading',
            unit='degC',
            active=False,
        )
        self.identity = TelemetrySourceIdentity(
            environment='s6-test',
            machine_pk=self.machine.pk,
            source_pk=self.source.pk,
            binding_pk=self.binding.pk,
        )
        self.window = HistoryWindow(-1, 4)
        self.record = observation(
            value={'v': [0, False, None]},
            source=self.identity,
            external_key=self.point.path,
            unit='degC',
        )
        self.mapping = FixtureMapping(
            client_pk=self.tenant.pk,
            station_pk=self.station.pk,
            dictionary_point_pk=self.point.pk,
            station_uuid=str(self.station.source_entity_uuid),
            external_key=self.point.path,
            unit=self.point.unit,
            dictionary_hash=self.binding.dictionary_hash,
            effective_window=self.window,
        )
        self.adapter = FixtureHistoryAdapter.from_records(
            source=self.identity,
            mapping=self.mapping,
            coverage=self.window,
            generation='fixture-generation',
            records=(self.record,),
        )

    def request(self):
        """Bind a native DRF request with explicitly synthetic forced transport."""
        raw = APIRequestFactory().get('/synthetic-s6-history/')
        force_authenticate(raw, user=self.actor)
        return Request(raw)

    def oauth_request(self):
        """Use the real OAuth2 authenticator with a disposable synthetic token."""
        from oauth2_provider.contrib.rest_framework.authentication import (
            OAuth2Authentication,
        )
        from oauth2_provider.models import get_access_token_model

        from users.oauth2_scopes import get_granular_scope

        literal = f's6-synthetic-local-token-{self.actor.pk}'
        self.token = get_access_token_model().objects.create(
            user=self.actor,
            token=literal,
            scope=get_granular_scope('view', 'work_order'),
            expires=timezone.now() + timedelta(hours=1),
        )
        raw = APIRequestFactory().get(
            '/synthetic-s6-oauth/', HTTP_AUTHORIZATION='Bearer ' + literal
        )
        return Request(raw, authenticators=[OAuth2Authentication()])

    def read(self, request=None, **changes):
        """Enter the actual request reader, not a direct DTO/always-true fallback."""
        from machine_health.services.integration_s6_reader import read_recorded_history

        arguments = {
            'environment': 's6-test',
            'machine_pk': self.machine.pk,
            'source_pk': self.source.pk,
            'binding_pk': self.binding.pk,
            'window': self.window,
            'adapter': self.adapter,
        }
        arguments.update(changes)
        return read_recorded_history(
            self.request() if request is None else request, **arguments
        )


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-s6-fixture-fallback',
)
class AuthenticatedHistoryReadTests(S6RequestFixture, TestCase):
    """Synthetic transport does not replace real current actor/native authority."""

    def test_authenticated_nonempty_history_preserves_producer_provenance(self):
        """One original inactive mapping admits only its actual recorded fixture."""
        from machine_health.services import integration_s6_wire as wire

        outcome = self.read()
        self.assertEqual(outcome.state, 'available')
        assert_literal_bool(self, outcome.population_complete, False)
        assert_literal_bool(self, outcome.revision_complete, False)
        body = json.loads(outcome.payload)
        self.assertEqual(body['schema_version'], 'aimms.s6.history-source.v1')
        self.assertEqual(
            body['observations'], [json.loads(wire.encode_observation(self.record))]
        )
        self.assertEqual(body['context']['actor_pk'], self.actor.pk)
        self.assertEqual(body['context']['source_pk'], self.source.pk)
        self.assertEqual(body['context']['binding_pk'], self.binding.pk)
        self.assertTrue(body['complete'])
        self.assertEqual(body['gaps'], [])
        self.assertFalse(body['recorded_telemetry_as_live'])
        self.assertFalse(body['display_shifted'])
        self.assertEqual(body['mapping']['evidence_kind'], 'synthetic_fixture')

    def test_lost_explicit_grant_prevents_source_dispatch_and_payload(self):
        """The request's current role alone cannot authorize a source value."""
        self.grant.delete()
        with patch.object(FixtureHistoryAdapter, 'read_window') as source_read:
            outcome = self.read()
        self.assertEqual(outcome.state, 'unavailable')
        self.assertIsNone(outcome.payload)
        source_read.assert_not_called()

    def test_unbound_anonymous_and_mutation_requests_do_not_dispatch(self):
        """A label or user-like object is not real request intake."""
        raw = APIRequestFactory().post('/synthetic-s6/')
        force_authenticate(raw, user=self.actor)
        for request in (
            SimpleNamespace(user=self.actor, method='GET'),
            Request(APIRequestFactory().get('/anonymous/'), authenticators=[]),
            Request(raw),
        ):
            with self.subTest(kind=type(request).__name__):
                with patch.object(FixtureHistoryAdapter, 'read_window') as source_read:
                    outcome = self.read(request=request)
                self.assertEqual(outcome.state, 'unavailable')
                self.assertIsNone(outcome.payload)
                source_read.assert_not_called()

    def test_native_scope_and_explicit_grant_are_independent(self):
        """Neither a current grant nor an independently positive native scope suffices."""
        from tasks.scope import MaintenanceScope

        allowed = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk)
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: allowed):
            self.assertEqual(self.read().state, 'available')
            self.grant.delete()
            self.assertEqual(self.read().state, 'unavailable')
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.tenant
        )
        foreign = {
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.foreign_tenant.pk
            )
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: foreign):
            self.assertTrue(ClientScopeGrant.objects.filter(pk=self.grant.pk).exists())
            self.assertEqual(self.read().state, 'unavailable')
        self.assertEqual(self.read().state, 'available')

    def test_superuser_still_needs_the_explicit_source_client_grant(self):
        """Native superuser access is not an integration grant."""
        get_user_model().objects.filter(pk=self.actor.pk).update(is_superuser=True)
        self.assertEqual(self.read().state, 'available')
        self.grant.delete()
        self.assertEqual(self.read().state, 'unavailable')

    def test_role_and_account_are_reloaded_not_taken_from_request_cache(self):
        """Current native account and role control every repeated request."""
        request = self.request()
        self.assertEqual(self.read(request=request).state, 'available')
        RuleSet.objects.filter(pk=self.rule.pk).update(can_view=False)
        self.assertEqual(self.read(request=request).state, 'unavailable')
        RuleSet.objects.filter(pk=self.rule.pk).update(can_view=True)
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
        self.assertTrue(self.actor.is_active)
        self.assertEqual(self.read(request=request).state, 'unavailable')

    def test_real_oauth_authentication_and_current_token_scope_are_required(self):
        """Real local bearer authentication and stored granular scope both run."""
        from oauth2_provider.models import get_access_token_model

        request = self.oauth_request()
        self.assertEqual(self.read(request=request).state, 'available')
        get_access_token_model().objects.filter(pk=self.token.pk).update(scope='write')
        self.assertEqual(self.read(request=request).state, 'unavailable')
        get_access_token_model().objects.filter(pk=self.token.pk).delete()
        self.assertEqual(self.read(request=request).state, 'unavailable')

    def test_revocation_during_encoding_withholds_both_value_and_metadata(self):
        """Final request/source checks occur after the actual codec returns bytes."""
        from machine_health.services import integration_s6_wire as wire

        original = wire.encode_history_read

        def revoke(*args, **kwargs):
            payload = original(*args, **kwargs)
            self.grant.delete()
            return payload

        self.assertEqual(self.read().state, 'available')
        with patch.object(wire, 'encode_history_read', new=revoke):
            result = self.read()
        self.assertEqual(result.state, 'unavailable')
        self.assertIsNone(result.payload)

    def test_revocation_during_outcome_construction_withholds_output(self):
        """The final outer DTO constructor is a separate fallible work boundary."""
        from machine_health.services import integration_s6_reader as reader

        original = reader.S6ReadOutcome.__post_init__

        def revoke(result):
            original(result)
            if result.state == 'available':
                get_user_model().objects.filter(pk=self.actor.pk).update(
                    is_active=False
                )

        self.assertEqual(self.read().state, 'available')
        with patch.object(reader.S6ReadOutcome, '__post_init__', new=revoke):
            result = self.read()
        self.assertEqual(result.state, 'unavailable')
        self.assertIsNone(result.payload)

    def test_known_or_unknown_source_faults_are_not_empty_success(self):
        """Authorized faults are generic faults; revoked authority hides them."""
        from machine_health.services.integration_history import HistoryAdapterError

        with patch.object(
            FixtureHistoryAdapter,
            'read_window',
            side_effect=RuntimeError('s6-private-source-detail'),
        ):
            with self.assertRaises(HistoryAdapterError) as raised:
                self.read()
        self.assertNotIn('s6-private-source-detail', str(raised.exception))

        def revoked(*args, **kwargs):
            self.grant.delete()
            raise RuntimeError('s6-private-source-detail')

        with patch.object(FixtureHistoryAdapter, 'read_window', new=revoked):
            result = self.read()
        self.assertEqual(result.state, 'unavailable')
        self.assertIsNone(result.payload)

    def test_corrupted_encoder_payload_is_a_fault_while_authorized(self):
        """Wrong schema, malformed bytes and None cannot become available history."""
        from machine_health.services import integration_s6_wire as wire

        original = wire.encode_history_read

        def wrong(*args, **kwargs):
            body = json.loads(original(*args, **kwargs))
            body['schema_version'] = 'wrong-history-schema'
            return json.dumps(body).encode()

        for encoder in (wrong, lambda *args, **kwargs: None):
            with self.subTest(encoder=encoder):
                with patch.object(wire, 'encode_history_read', new=encoder):
                    with self.assertRaises(wire.SourceSerializationError):
                        self.read()

    def test_mapping_source_or_client_transfer_invalidates_current_authority(self):
        """Historical data is not reassigned by a current machine/client edit."""
        from assets.models import AssetMachine

        self.assertEqual(self.read().state, 'available')
        DictionaryPoint.objects.filter(pk=self.point.pk).update(status='pending')
        self.assertEqual(self.read().state, 'unavailable')
        DictionaryPoint.objects.filter(pk=self.point.pk).update(status='approved')
        self.assertEqual(self.read().state, 'available')
        HealthSource.objects.filter(pk=self.source.pk).update(config={'stations': []})
        self.assertEqual(self.read().state, 'unavailable')
        HealthSource.objects.filter(pk=self.source.pk).update(config=self.source.config)
        AssetMachine.objects.filter(pk=self.machine.pk).update(
            client=self.foreign_tenant
        )
        self.assertEqual(self.read().state, 'unavailable')

    def test_missing_adapter_and_unknown_mapping_are_explicit_unsupported(self):
        """Capability refusal returns no payload and dispatches no source reads."""
        unknown = replace(self.adapter, mapping=replace(self.mapping, status='unknown'))
        with patch.object(FixtureHistoryAdapter, 'read_window') as read:
            for adapter in (None, unknown):
                result = self.read(adapter=adapter)
                self.assertEqual(result.state, 'unsupported')
                self.assertIsNone(result.payload)
            read.assert_not_called()

    def test_token_revoked_inside_error_source_recheck_withholds_fault(self):
        """Fresh request authority must follow a fallible error-path mapping read."""
        from oauth2_provider.models import get_access_token_model

        from machine_health.services import integration_s6_reader as reader
        from machine_health.services import integration_s6_wire as wire

        request = self.oauth_request()
        self.assertEqual(self.read(request=request).state, 'available')
        original = reader._mapping_matches
        failed = False

        def fault(*args, **kwargs):
            nonlocal failed
            failed = True
            raise wire.SourceSerializationError('s6-private-source-detail')

        def revoke(*args, **kwargs):
            if failed:
                get_access_token_model().objects.filter(pk=self.token.pk).delete()
            return original(*args, **kwargs)

        with patch.object(wire, 'encode_history_read', new=fault):
            with patch.object(reader, '_mapping_matches', new=revoke):
                result = self.read(request=request)
        self.assertEqual(result.state, 'unavailable')
        self.assertIsNone(result.payload)

    def test_final_mapping_observation_fault_is_not_escaped_or_empty_success(self):
        """Use a healthy trace to reach the last source observer specifically."""
        from machine_health.services import integration_s6_reader as reader

        original = reader._mapping_matches
        healthy = []

        def observe(*args, **kwargs):
            healthy.append(1)
            return original(*args, **kwargs)

        with patch.object(reader, '_mapping_matches', new=observe):
            self.assertEqual(self.read().state, 'available')
        calls = []

        def fail_last(*args, **kwargs):
            calls.append(1)
            if len(calls) == len(healthy):
                self.grant.delete()
                raise RuntimeError('s6-private-final-observer')
            return original(*args, **kwargs)

        with patch.object(reader, '_mapping_matches', new=fail_last):
            result = self.read()
        self.assertEqual(len(calls), len(healthy))
        self.assertEqual(result.state, 'unavailable')
        self.assertIsNone(result.payload)

    def test_station_native_scope_is_required_independently_of_machine(self):
        """A selected pump cannot lend its permission to its related station."""
        from tasks import scope

        original = scope.enforce_machine_scope
        seen = []

        def narrower(actor, machine):
            seen.append(machine.pk)
            if machine.pk == self.station.pk:
                raise scope.ScopeError('synthetic station denial')
            return original(actor, machine)

        self.assertEqual(self.read().state, 'available')
        with patch.object(scope, 'enforce_machine_scope', new=narrower):
            with patch.object(FixtureHistoryAdapter, 'read_window') as source:
                result = self.read()
        self.assertEqual(result.state, 'unavailable')
        self.assertIn(self.machine.pk, seen)
        self.assertIn(self.station.pk, seen)
        source.assert_not_called()

    def test_allowed_output_excludes_source_config_and_credential_references(self):
        """Allowed machine data is not approval for deployment/configuration data."""
        config = dict(self.source.config, unrelated='s6-private-config-sentinel')
        HealthSource.objects.filter(pk=self.source.pk).update(
            config=config, secret_ref='s6-private-reference-sentinel'
        )
        result = self.read()
        self.assertEqual(result.state, 'available')
        self.assertNotIn(b's6-private-config-sentinel', result.payload)
        self.assertNotIn(b's6-private-reference-sentinel', result.payload)

    def test_point_revoked_inside_last_mapping_read_withholds_output(self):
        """An independently positive mapping comparison is not point approval."""
        from machine_health.services import integration_s6_reader as reader

        original = reader._mapping_matches
        healthy = []

        def trace(*args, **kwargs):
            healthy.append(1)
            return original(*args, **kwargs)

        with patch.object(reader, '_mapping_matches', new=trace):
            self.assertEqual(self.read().state, 'available')
        calls = []

        def revoke(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(1)
            if len(calls) == len(healthy):
                DictionaryPoint.objects.filter(pk=self.point.pk).update(
                    status='pending'
                )
            return result

        with patch.object(reader, '_mapping_matches', new=revoke):
            result = self.read()
        self.assertEqual(len(calls), len(healthy))
        self.assertEqual(result.state, 'unavailable')
        self.assertIsNone(result.payload)

    def test_declared_revocation_matrix_with_restored_native_positives(self):
        """Cross native authority changes with each source-owned fallible boundary."""
        from oauth2_provider.models import get_access_token_model
        from tasks.scope import granted_client_scope_resolver

        from assets.models import AssetMachine
        from machine_health.services import integration_s6_adapter as adapter_module
        from machine_health.services import integration_s6_reader as reader
        from machine_health.services import integration_s6_wire as wire

        request = self.oauth_request()
        scope = {'revoked': False}
        token_model = get_access_token_model()

        def native_scopes(actor):
            return set() if scope['revoked'] else granted_client_scope_resolver(actor)

        specifications = (
            ('actor_inactive', get_user_model(), self.actor.pk, {'is_active': False}),
            ('role_removed', RuleSet, self.rule.pk, {'can_view': False}),
            ('client_inactive', Client, self.tenant.pk, {'active': False}),
            (
                'grant_reassigned',
                ClientScopeGrant,
                self.grant.pk,
                {'client_id': self.foreign_tenant.pk},
            ),
            ('machine_inactive', AssetMachine, self.machine.pk, {'active': False}),
            (
                'machine_transferred',
                AssetMachine,
                self.machine.pk,
                {'client_id': self.foreign_tenant.pk},
            ),
            ('station_inactive', AssetMachine, self.station.pk, {'active': False}),
            (
                'station_transferred',
                AssetMachine,
                self.station.pk,
                {'client_id': self.foreign_tenant.pk},
            ),
            (
                'station_identity_changed',
                AssetMachine,
                self.station.pk,
                {'source_entity_uuid': uuid4()},
            ),
            ('source_inactive', HealthSource, self.source.pk, {'active': False}),
            (
                'source_transferred',
                HealthSource,
                self.source.pk,
                {'client_id': self.foreign_tenant.pk},
            ),
            (
                'source_connector_changed',
                HealthSource,
                self.source.pk,
                {'connector_type': 'webhook'},
            ),
            (
                'source_stations_changed',
                HealthSource,
                self.source.pk,
                {'config': {'stations': []}},
            ),
            (
                'binding_path_changed',
                MachineSignalBinding,
                self.binding.pk,
                {'external_key': '/s6/unreviewed'},
            ),
            (
                'binding_unit_changed',
                MachineSignalBinding,
                self.binding.pk,
                {'unit': 'm'},
            ),
            (
                'binding_hash_changed',
                MachineSignalBinding,
                self.binding.pk,
                {'dictionary_hash': 's6-unreviewed-hash'},
            ),
            (
                'binding_point_removed',
                MachineSignalBinding,
                self.binding.pk,
                {'dictionary_point_id': None},
            ),
            ('point_unapproved', DictionaryPoint, self.point.pk, {'status': 'pending'}),
            (
                'point_unit_unreviewed',
                DictionaryPoint,
                self.point.pk,
                {'unit_status': 'unknown'},
            ),
            (
                'point_type_unknown',
                DictionaryPoint,
                self.point.pk,
                {'data_type': 'unknown'},
            ),
            (
                'point_path_changed',
                DictionaryPoint,
                self.point.pk,
                {'path': '/s6/unreviewed'},
            ),
            ('point_unit_changed', DictionaryPoint, self.point.pk, {'unit': 'm'}),
            (
                'point_machine_changed',
                DictionaryPoint,
                self.point.pk,
                {'machine_id': self.station.pk},
            ),
            (
                'point_station_changed',
                DictionaryPoint,
                self.point.pk,
                {'station_id': self.machine.pk},
            ),
            ('token_scope_removed', token_model, self.token.pk, {'scope': 'write'}),
            (
                'token_expired',
                token_model,
                self.token.pk,
                {'expires': timezone.now() - timedelta(hours=1)},
            ),
        )

        def update(model, pk, fields):
            self.assertEqual(model.objects.filter(pk=pk).update(**fields), 1)

        operations: list[tuple[str, Callable[[], object]]] = [
            (label, lambda model=model, pk=pk, fields=fields: update(model, pk, fields))
            for label, model, pk, fields in specifications
        ]
        operations.extend((
            (
                'grant_deleted',
                lambda: ClientScopeGrant.objects.filter(pk=self.grant.pk).delete(),
            ),
            (
                'token_deleted',
                lambda: token_model.objects.filter(pk=self.token.pk).delete(),
            ),
            ('native_scope_revoked', lambda: scope.update(revoked=True)),
        ))
        stages = (
            'post_read',
            'post_capture',
            'post_encode',
            'post_outcome',
            'post_final_payload_check',
            'last_mapping_read',
            'encoding_fault',
            'error_mapping_read',
        )
        original_mapping = reader._mapping_matches
        original_check = wire.check_history_payload
        original_encode = wire.encode_history_read
        original_capture = adapter_module.FixtureHistoryRead
        original_outcome = reader.S6ReadOutcome
        original_read = FixtureHistoryAdapter.read_window
        healthy = {'mapping': 0, 'payload': 0}

        def trace_mapping(*args, **kwargs):
            healthy['mapping'] += 1
            return original_mapping(*args, **kwargs)

        def trace_payload(*args, **kwargs):
            healthy['payload'] += 1
            return original_check(*args, **kwargs)

        observations = []
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=native_scopes):
            with patch.object(reader, '_mapping_matches', new=trace_mapping):
                with patch.object(wire, 'check_history_payload', new=trace_payload):
                    self.assertEqual(self.read(request=request).state, 'available')
            self.assertGreater(healthy['mapping'], 0)
            self.assertGreater(healthy['payload'], 0)
            for stage in stages:
                for label, mutation in operations:
                    with self.subTest(stage=stage, mutation=label):
                        fired, calls = [], {'mapping': 0, 'payload': 0, 'fault': False}
                        savepoint = transaction.savepoint()

                        def activate(operation=mutation, fired=fired):
                            if not fired:
                                operation()
                                fired.append(True)

                        def read(*args, _activate=activate, **kwargs):
                            value = original_read(*args, **kwargs)
                            _activate()
                            return value

                        def capture(*args, _activate=activate, **kwargs):
                            value = original_capture(*args, **kwargs)
                            _activate()
                            return value

                        def encode(
                            *args,
                            _stage=stage,
                            _calls=calls,
                            _activate=activate,
                            **kwargs,
                        ):
                            if _stage in ('encoding_fault', 'error_mapping_read'):
                                _calls['fault'] = True
                                if _stage == 'encoding_fault':
                                    _activate()
                                raise wire.SourceSerializationError(
                                    's6-private-matrix-fault'
                                )
                            value = original_encode(*args, **kwargs)
                            _activate()
                            return value

                        def outcome(*args, _activate=activate, **kwargs):
                            value = original_outcome(*args, **kwargs)
                            if value.state == 'available':
                                _activate()
                            return value

                        def check(*args, _calls=calls, _activate=activate, **kwargs):
                            value = original_check(*args, **kwargs)
                            _calls['payload'] += 1
                            if _calls['payload'] == healthy['payload']:
                                _activate()
                            return value

                        def mapping(
                            *args,
                            _stage=stage,
                            _calls=calls,
                            _activate=activate,
                            **kwargs,
                        ):
                            value = original_mapping(*args, **kwargs)
                            _calls['mapping'] += 1
                            if (
                                _stage == 'last_mapping_read'
                                and _calls['mapping'] == healthy['mapping']
                            ) or (_stage == 'error_mapping_read' and _calls['fault']):
                                _activate()
                            return value

                        try:
                            with ExitStack() as stack:
                                target = {
                                    'post_read': (
                                        FixtureHistoryAdapter,
                                        'read_window',
                                        read,
                                    ),
                                    'post_capture': (
                                        adapter_module,
                                        'FixtureHistoryRead',
                                        capture,
                                    ),
                                    'post_encode': (
                                        wire,
                                        'encode_history_read',
                                        encode,
                                    ),
                                    'post_outcome': (reader, 'S6ReadOutcome', outcome),
                                    'post_final_payload_check': (
                                        wire,
                                        'check_history_payload',
                                        check,
                                    ),
                                    'last_mapping_read': (
                                        reader,
                                        '_mapping_matches',
                                        mapping,
                                    ),
                                    'encoding_fault': (
                                        wire,
                                        'encode_history_read',
                                        encode,
                                    ),
                                    'error_mapping_read': (
                                        wire,
                                        'encode_history_read',
                                        encode,
                                    ),
                                }[stage]
                                stack.enter_context(
                                    patch.object(target[0], target[1], new=target[2])
                                )
                                if stage == 'error_mapping_read':
                                    stack.enter_context(
                                        patch.object(
                                            reader, '_mapping_matches', new=mapping
                                        )
                                    )
                                result = self.read(request=request)
                        finally:
                            transaction.savepoint_rollback(savepoint)
                            scope['revoked'] = False
                        # Restoration happens before any assertion can skip the next case.
                        self.assertEqual(self.read(request=request).state, 'available')
                        self.assertEqual(fired, [True])
                        self.assertEqual(result.state, 'unavailable')
                        self.assertIsNone(result.payload)
                        observations.append({
                            'stage': stage,
                            'mutation': label,
                            'activated': True,
                            'withheld': True,
                            'restored_positive': True,
                        })
        self.assertEqual(len(observations), len(stages) * len(operations))
        with (Path(tempfile.gettempdir()) / 's6-revocation-matrix.json').open(
            'x'
        ) as handle:
            json.dump(
                {
                    'stages': stages,
                    'mutations': [row[0] for row in operations],
                    'observations': observations,
                    'healthy_trace': healthy,
                },
                handle,
                indent=2,
            )
