"""Actual S1 request intake and S3 source-outcome consumption controls."""

from integration_state.tests.assertions import assert_literal_bool

import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from tasks import integration_s3_reader as reader
from tasks import integration_s3_wire as wire
from tasks.tests.test_integration_s3_wire_db import WireFixture


class RequestFixture(WireFixture):
    """Build a real DRF request; a caller-supplied actor label is not intake."""

    def request(self):
        """Use native DRF forced authentication only as a synthetic transport."""
        raw = APIRequestFactory().get('/synthetic-s3-source/')
        force_authenticate(raw, user=self.actor)
        return Request(raw)

    def read(self, request=None, operation='work.core', source_pk=None, **kwargs):
        """Consume the actual request boundary, never a direct-reader fallback."""
        return reader.read_source(
            request if request is not None else self.request(),
            environment='s3-test',
            operation=operation,
            source_pk=self.work.pk if source_pk is None else source_pk,
            **kwargs,
        )


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-s3-test-client',
)
class RealRequestReadTests(RequestFixture, TestCase):
    """Use real withholding, native scope/current grants and authenticated intake."""

    def test_unproven_history_is_explicitly_unavailable_not_complete_empty(self):
        """A source None must never imply empty population, a tombstone or completion."""
        with patch('django.core.files.storage.default_storage.open') as opened:
            result = self.read()
        self.assertEqual(getattr(result, 'state', None), 'unavailable')
        self.assertIsNone(result.payload)
        assert_literal_bool(self, result.population_complete, False)
        assert_literal_bool(self, result.revision_complete, False)
        self.assertEqual(result.schema_version, 'aimms.s3.read-result.v1')
        opened.assert_not_called()


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-s3-test-client',
)
@assume_synthetic_attribution
class SimulatedRequestReadTests(RequestFixture, TestCase):
    """Exercise actual request/source/encoding composition, not historical admission."""

    def test_all_six_reads_preserve_actual_source_payloads(self):
        """The consumer retains exact producer JSON, binary/hash/class/domain data."""
        names = {
            'read_work_details_json': 'work.core',
            'read_maintenance_details_json': 'maintenance.direct',
            'read_work_document_metadata_json': 'work_document.metadata',
            'read_machine_document_metadata_json': 'machine_document.metadata',
            'read_work_document_content_json': 'work_document.content',
            'read_machine_document_content_json': 'machine_document.content',
        }
        for operation, kwargs, owner in self.prepare_operations():
            with self.subTest(operation=operation):
                self.select_owner(owner)
                expected = getattr(wire, operation)(
                    self.actor, environment='s3-test', **kwargs
                )
                self.assertIs(type(expected), bytes)
                pk = next(
                    kwargs[key]
                    for key in ('work_pk', 'record_pk', 'attachment_pk')
                    if key in kwargs
                )
                result = self.read(
                    operation=names[operation],
                    source_pk=pk,
                    max_bytes=kwargs.get('max_bytes'),
                )
                self.assertEqual(result.state, 'available')
                self.assertEqual(result.payload, expected)
                assert_literal_bool(self, result.population_complete, False)
                assert_literal_bool(self, result.revision_complete, False)

    def test_account_revoked_during_outcome_construction_withholds_data(self):
        """Final construction follows source encoding and needs fresh request intake."""
        from django.contrib.auth import get_user_model

        self.assertEqual(self.read().state, 'available')
        original = reader.S3ReadOutcome.__post_init__

        def revoked(result):
            original(result)
            if result.state == 'available':
                get_user_model().objects.filter(pk=self.actor.pk).update(
                    is_active=False
                )

        with patch.object(reader.S3ReadOutcome, '__post_init__', new=revoked):
            result = self.read()
        self.assertEqual(result.state, 'unavailable')
        self.assertIsNone(result.payload)
        self.assertTrue(self.actor.is_active)

    def test_grant_revoked_during_outcome_construction_withholds_data(self):
        """Request authentication alone is not the source's current client grant."""
        self.assertEqual(self.read().state, 'available')
        original = reader.S3ReadOutcome.__post_init__

        def revoked(result):
            original(result)
            if result.state == 'available':
                self.grant.delete()

        with patch.object(reader.S3ReadOutcome, '__post_init__', new=revoked):
            result = self.read()
        self.assertEqual(result.state, 'unavailable')
        self.assertIsNone(result.payload)

    def test_lost_request_authority_hides_a_source_fault(self):
        """An encoded-source failure cannot escape after the active account is lost."""
        from django.contrib.auth import get_user_model

        self.assertEqual(self.read().state, 'available')
        original = wire.read_work_details_json
        calls = 0

        def failed(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                get_user_model().objects.filter(pk=self.actor.pk).update(
                    is_active=False
                )
                raise wire.SourceSerializationError('Private synthetic source failure')
            return original(*args, **kwargs)

        with patch.object(wire, 'read_work_details_json', new=failed):
            try:
                result = reader.read_source(
                    self.request(),
                    environment='s3-test',
                    operation='work.core',
                    source_pk=self.work.pk,
                )
            except wire.SourceSerializationError:
                self.fail('Source fault escaped after request authority loss')
        self.assertEqual(result.state, 'unavailable')
        self.assertIsNone(result.payload)

    def test_unknown_wire_schema_is_a_fault_not_success_or_unavailable(self):
        """A gateway cannot turn an invalid producer version into opaque empty data."""
        self.assertEqual(self.read().state, 'available')
        original = wire.read_work_details_json
        calls = 0

        def invalid(*args, **kwargs):
            nonlocal calls
            calls += 1
            payload = original(*args, **kwargs)
            if calls == 2:
                body = json.loads(payload)
                body['wire_schema'] = 'wrong-source-version'
                return json.dumps(body).encode('utf-8')
            return payload

        with patch.object(wire, 'read_work_details_json', new=invalid):
            with self.assertRaises(wire.SourceSerializationError):
                reader.read_source(
                    self.request(),
                    environment='s3-test',
                    operation='work.core',
                    source_pk=self.work.pk,
                )

    def test_native_source_faults_respect_current_request_authority(self):
        """Known source errors stay errors while authorized, opaque after revocation."""
        from django.contrib.auth import get_user_model

        from assets.integration_sources import (
            AttachmentContentAbsentError,
            AttachmentContentLimitError,
            AttachmentContentUnavailableError,
        )
        from tasks.integration_s3_details import DirectDetailsUnavailableError

        operations = self.prepare_operations()
        self.select_owner(
            next(
                owner
                for name, _, owner in operations
                if name == 'read_work_document_content_json'
            )
        )
        failures = (
            AttachmentContentAbsentError,
            AttachmentContentLimitError,
            AttachmentContentUnavailableError,
            DirectDetailsUnavailableError,
        )
        for fault in failures:
            direct = fault is DirectDetailsUnavailableError
            operation = 'work.core' if direct else 'work_document.content'
            function = (
                'read_work_details_json'
                if direct
                else 'read_work_document_content_json'
            )
            source_pk = self.work.pk if direct else self.attachment.pk
            kwargs = {} if direct else {'max_bytes': 128}
            self.assertEqual(
                self.read(operation=operation, source_pk=source_pk, **kwargs).state,
                'available',
            )
            original = getattr(wire, function)
            for revoked in (False, True):
                with self.subTest(fault=fault.__name__, revoked=revoked):
                    case = {
                        'calls': 0,
                        'trigger': 2 if direct else 1,
                        'revoked': revoked,
                        'fault': fault,
                        'source': original,
                    }

                    def failed(*args, _case=case, **reader_kwargs):
                        _case['calls'] += 1
                        if _case['calls'] == _case['trigger']:
                            if _case['revoked']:
                                get_user_model().objects.filter(
                                    pk=self.actor.pk
                                ).update(is_active=False)
                            raise _case['fault'](
                                'Synthetic private native source failure'
                            )
                        return _case['source'](*args, **reader_kwargs)

                    try:
                        with patch.object(wire, function, new=failed):
                            if not revoked:
                                with self.assertRaises(fault):
                                    self.read(
                                        operation=operation,
                                        source_pk=source_pk,
                                        **kwargs,
                                    )
                            else:
                                try:
                                    result = self.read(
                                        operation=operation,
                                        source_pk=source_pk,
                                        **kwargs,
                                    )
                                except fault:
                                    self.fail(
                                        'Native source fault escaped after request revocation'
                                    )
                                self.assertEqual(result.state, 'unavailable')
                                self.assertIsNone(result.payload)
                    finally:
                        get_user_model().objects.filter(pk=self.actor.pk).update(
                            is_active=True
                        )

    def test_actual_intake_token_row_is_rechecked_after_construction(self):
        """Synthetic OAuth transport exercises native token-row policy, not HTTP auth."""
        from datetime import timedelta

        from django.utils import timezone

        from oauth2_provider.contrib.rest_framework.authentication import (
            OAuth2Authentication,
        )
        from oauth2_provider.models import get_access_token_model

        from users.oauth2_scopes import get_granular_scope

        actor = self.actor
        token_type = get_access_token_model()
        token = token_type.objects.create(
            user=actor,
            token='s3-synthetic-local-token',
            scope=get_granular_scope('view', 'work_order'),
            expires=timezone.now() + timedelta(hours=1),
        )

        class SyntheticOAuth(OAuth2Authentication):
            def authenticate(self, request):
                return actor, token

        request = Request(
            APIRequestFactory().get('/synthetic-s3-oauth/'),
            authenticators=[SyntheticOAuth()],
        )
        original = reader.S3ReadOutcome.__post_init__
        for change in ('scope', 'expiry', 'delete'):
            with self.subTest(change=change):
                token_type.objects.filter(pk=token.pk).update(
                    scope=get_granular_scope('view', 'work_order'),
                    expires=timezone.now() + timedelta(hours=1),
                )
                self.assertEqual(self.read(request=request).state, 'available')

                def revoked(result, _change=change):
                    original(result)
                    if result.state != 'available':
                        return
                    row = token_type.objects.filter(pk=token.pk)
                    if _change == 'scope':
                        row.update(scope='write')
                    elif _change == 'expiry':
                        row.update(expires=timezone.now() - timedelta(seconds=1))
                    else:
                        row.delete()

                with patch.object(reader.S3ReadOutcome, '__post_init__', new=revoked):
                    result = self.read(request=request)
                self.assertEqual(result.state, 'unavailable')
                self.assertIsNone(result.payload)

    def test_unbound_and_non_get_contexts_never_read_source(self):
        """Actor labels are not a substitute for S1's actual authenticated intake."""
        from types import SimpleNamespace

        raw = APIRequestFactory().post('/synthetic-s3-source/')
        force_authenticate(raw, user=self.actor)
        for request in (
            self.actor,
            {'user': self.actor},
            SimpleNamespace(user=self.actor, method='GET'),
            Request(APIRequestFactory().get('/synthetic-unbound/'), authenticators=[]),
            Request(raw),
        ):
            with self.subTest(request_type=type(request).__name__):
                with patch.object(wire, 'read_work_details_json') as source:
                    result = reader.read_source(
                        request,
                        environment='s3-test',
                        operation='work.core',
                        source_pk=self.work.pk,
                    )
                self.assertEqual(result.state, 'unavailable')
                source.assert_not_called()

    def test_source_change_after_construction_withholds_old_observation(self):
        """A current request is not permission to emit an earlier changed source."""
        from tasks.models import WorkOrder

        self.assertEqual(self.read().state, 'available')
        original = reader.S3ReadOutcome.__post_init__

        def changed(result):
            original(result)
            if result.state == 'available':
                WorkOrder.objects.filter(pk=self.work.pk).update(title='Changed source')

        with patch.object(reader.S3ReadOutcome, '__post_init__', new=changed):
            result = self.read()
        self.assertEqual(result.state, 'unavailable')
        self.assertIsNone(result.payload)

    def test_final_observation_fault_refreshes_request_authority(self):
        """The final source check is another fallible read, not a trusted epilogue."""
        from django.contrib.auth import get_user_model

        self.assertEqual(self.read().state, 'available')
        original = wire.read_work_details_json
        for revoked in (False, True):
            with self.subTest(revoked=revoked):
                case = {'calls': 0, 'revoked': revoked}

                def failed(*args, _case=case, **kwargs):
                    _case['calls'] += 1
                    if _case['calls'] == 3:
                        if _case['revoked']:
                            get_user_model().objects.filter(pk=self.actor.pk).update(
                                is_active=False
                            )
                        raise wire.SourceSerializationError(
                            'Synthetic private final-observation fault'
                        )
                    return original(*args, **kwargs)

                try:
                    with patch.object(wire, 'read_work_details_json', new=failed):
                        if not revoked:
                            with self.assertRaises(wire.SourceSerializationError):
                                self.read()
                        else:
                            try:
                                result = self.read()
                            except wire.SourceSerializationError:
                                self.fail(
                                    'Final observation fault escaped after request revocation'
                                )
                            self.assertEqual(result.state, 'unavailable')
                            self.assertIsNone(result.payload)
                finally:
                    get_user_model().objects.filter(pk=self.actor.pk).update(
                        is_active=True
                    )

    def test_revocation_during_error_recheck_withholds_failure(self):
        """The error-path source recheck cannot substitute for final request intake."""
        from django.contrib.auth import get_user_model

        self.assertEqual(self.read().state, 'available')
        original = wire.read_work_details_json
        calls = 0

        def failed(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise wire.SourceSerializationError('Synthetic private source fault')
            payload = original(*args, **kwargs)
            if calls == 3:
                get_user_model().objects.filter(pk=self.actor.pk).update(
                    is_active=False
                )
            return payload

        with patch.object(wire, 'read_work_details_json', new=failed):
            try:
                result = self.read()
            except wire.SourceSerializationError:
                self.fail('Source fault escaped revocation during error recheck')
        self.assertEqual(result.state, 'unavailable')
        self.assertIsNone(result.payload)
