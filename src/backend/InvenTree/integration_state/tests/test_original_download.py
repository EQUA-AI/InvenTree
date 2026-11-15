"""Actual native HTTP original download, independent grants and late fences."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import SimpleTestCase, TestCase, override_settings

from rest_framework.test import APIClient
from tasks.scope import scoped_work_orders

from assets import integration_content_api
from assets.integration_sources import AttachmentContentUnavailableError
from assets.models import ClientScopeGrant
from common.models import Attachment
from company.models import Company
from integration_state.models import SourceNamespace
from integration_state.tests.test_native_authority import NativeAuthorityFixture


class NativeOriginalDownloadTests(NativeAuthorityFixture, TestCase):
    """Native sessions and actual future proof; no permission/source substitutes."""

    def setUp(self):
        """Use a shared Work but independently attested A-only original."""
        super().setUp()
        customer = Company.objects.create(
            name='Synthetic download customer', is_customer=True
        )
        self.new_work.customer = customer
        self.new_work.save(update_fields=['customer'])
        self.approve(self.attest())
        self.attachment = Attachment(
            model_type='workorder',
            model_id=self.new_work.pk,
            attachment=SimpleUploadedFile(
                'synthetic-download.bin', b'\x00exact original\xff'
            ),
        )
        self.attachment.save(rebuild=False)
        self.addCleanup(self.attachment.attachment.delete, save=False)
        self.proof = self.attest('attachment', self.attachment.pk)
        self.approve(self.proof)
        self.client = APIClient()
        self.assertTrue(
            self.client.login(
                username=self.owner.username, password='synthetic-source-password'
            )
        )
        self.url = f'/api/assets/integration-content/{self.attachment.pk}/'

    def assert_withheld(self, response):
        """Opaque HTTP failure cannot return bytes, file coordinates or raw errors."""
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b'exact original', response.content)
        if self.attachment.attachment.name:
            self.assertNotIn(self.attachment.attachment.name.encode(), response.content)

    def test_native_bytes_and_headers_and_no_source_override(self):
        """Only the configured namespace and current native GET actor select bytes."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'\x00exact original\xff')
        self.assertEqual(response['Content-Type'], 'application/octet-stream')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertEqual(
            self.client.get(self.url, {'environment': 'other'}).status_code, 400
        )
        self.assert_withheld(self.client.get('/api/assets/integration-content/999999/'))

    def test_native_metadata_visibility_is_insufficient_without_the_original_grant(
        self,
    ):
        """A shared Work and its visible metadata confer no A source grant."""
        foreign = get_user_model().objects.create_user(
            username='synthetic-download-foreign', password='synthetic-foreign-password'
        )
        foreign.groups.set(self.owner.groups.all())
        ClientScopeGrant.objects.create(user=foreign, client=self.client_b)
        self.assertTrue(
            scoped_work_orders(foreign).filter(pk=self.new_work.pk).exists()
        )
        client = APIClient()
        self.assertTrue(
            client.login(
                username=foreign.username, password='synthetic-foreign-password'
            )
        )
        self.assertEqual(
            client.get(f'/api/attachment/{self.attachment.pk}/').status_code, 200
        )
        with patch('django.core.files.storage.default_storage.open') as opened:
            self.assert_withheld(client.get(self.url))
        opened.assert_not_called()

    def test_default_off_and_absent_namespace_never_read_content(self):
        """Caller selection cannot bypass disabled or absent server enrollment."""
        with (
            override_settings(AIMMS_INTEGRATION_AUTHORITY_ENABLED=False),
            patch('django.core.files.storage.default_storage.open') as opened,
        ):
            self.assert_withheld(self.client.get(self.url))
        opened.assert_not_called()
        SourceNamespace.objects.filter(pk=self.namespace.pk).update(active=False)
        with patch('django.core.files.storage.default_storage.open') as opened:
            self.assert_withheld(self.client.get(self.url))
        opened.assert_not_called()

    def test_unproven_attachment_never_opens_storage(self):
        """A proven owner cannot authorize a newly born but unattested original."""
        other = Attachment(
            model_type='workorder',
            model_id=self.new_work.pk,
            attachment=SimpleUploadedFile('unproven.bin', b'Synthetic unproven'),
        )
        other.save(rebuild=False)
        self.addCleanup(other.attachment.delete, save=False)
        with patch('django.core.files.storage.default_storage.open') as opened:
            response = self.client.get(f'/api/assets/integration-content/{other.pk}/')
            self.assertEqual(response.status_code, 404)
        opened.assert_not_called()

    def test_grant_revoked_during_response_construction_withholds_original(self):
        """A ready private response is not delivered after current authority is lost."""

        def revoke(*args, **kwargs):
            response = HttpResponse(*args, **kwargs)
            self.owner_grant.delete()
            return response

        with patch.object(integration_content_api, 'HttpResponse', side_effect=revoke):
            self.assert_withheld(self.client.get(self.url))

    def test_same_path_mutation_during_response_construction_withholds_original(self):
        """Metadata equality cannot establish unchanged original bytes."""

        def mutate(*args, **kwargs):
            response = HttpResponse(*args, **kwargs)
            with self.attachment.attachment.storage.open(
                self.attachment.attachment.name, 'wb'
            ) as stream:
                stream.write(b'Changed original at same path')
            return response

        with patch.object(integration_content_api, 'HttpResponse', side_effect=mutate):
            self.assert_withheld(self.client.get(self.url))

    def test_actual_withdrawal_and_namespace_change_withhold_stored_identity(self):
        """A path or session cannot reuse the earlier approved source revision."""
        response = self.client.post(
            f'/api/assets/integration-authority/{self.proof.pk}/withdraw/',
            {
                'expected_request_digest': self.proof.binding_digest,
                'reason_reference': 'synthetic-download-withdrawal',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assert_withheld(self.client.get(self.url))
        SourceNamespace.objects.filter(pk=self.namespace.pk).update(active=False)
        self.assert_withheld(self.client.get(self.url))

    def test_direct_machine_owner_is_independently_authorized(self):
        """Machine originals use the machine reader rather than Work identity guessing."""
        self.attachment.model_type = 'assetmachine'
        self.attachment.model_id = self.machine.pk
        self.attachment.save(rebuild=False)
        self.approve(
            self.attest(
                'attachment', self.attachment.pk, key='synthetic-machine-download'
            )
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'\x00exact original\xff')

    def test_link_only_and_storage_fault_are_explicit_unavailable(self):
        """A URL is never fetched, and a storage fault cannot become empty success."""
        with patch(
            'django.core.files.storage.default_storage.open',
            side_effect=OSError('Private synthetic storage fault'),
        ):
            response = self.client.get(self.url)
            self.assert_withheld(response)
            self.assertNotIn(b'Private synthetic storage fault', response.content)
        self.attachment.attachment.delete(save=False)
        self.attachment.attachment = None
        self.attachment.link = 'https://example.invalid/do-not-fetch'
        self.attachment.save(rebuild=False)
        self.approve(
            self.attest(
                'attachment', self.attachment.pk, key='synthetic-link-only-download'
            )
        )
        with patch('django.core.files.storage.default_storage.open') as opened:
            self.assert_withheld(self.client.get(self.url))
        opened.assert_not_called()

    def test_invalid_server_namespace_is_opaque_without_storage_io(self):
        """Invalid retained source configuration is not a caller-selected fallback."""
        SourceNamespace.objects.filter(pk=self.namespace.pk).update(environment='')
        with patch('django.core.files.storage.default_storage.open') as opened:
            self.assert_withheld(self.client.get(self.url))
        opened.assert_not_called()

    def test_native_content_constructor_cannot_replace_original_bytes(self):
        """Pure construction must preserve the exact native storage observation."""
        from tasks import integration_s3_documents as documents

        arguments = {
            'environment': self.namespace.environment,
            'attachment_pk': self.attachment.pk,
            'max_bytes': 128,
        }
        self.assertEqual(
            documents.read_work_document_content(self.owner, **arguments).content,
            b'\x00exact original\xff',
        )
        original = documents.DocumentContentSourceRecord.__post_init__

        def replace(record):
            original(record)
            object.__setattr__(record, 'content', b'Forged private projection')

        with (
            patch.object(
                documents.DocumentContentSourceRecord, '__post_init__', new=replace
            ),
            self.assertRaises(AttachmentContentUnavailableError),
        ):
            documents.read_work_document_content(self.owner, **arguments)

    def test_native_wire_withdrawal_cannot_emit_a_previously_allowed_original(self):
        """The pure encoder is followed by a fresh original-owner/proof fence."""
        from tasks import integration_s3_wire as wire

        arguments = {
            'environment': self.namespace.environment,
            'attachment_pk': self.attachment.pk,
            'max_bytes': 128,
        }
        self.assertIsInstance(
            wire.read_work_document_content_json(self.owner, **arguments), bytes
        )
        encode = wire.encode_source_record

        def withdraw(record):
            encoded = encode(record)
            result = self.client.post(
                f'/api/assets/integration-authority/{self.proof.pk}/withdraw/',
                {
                    'expected_request_digest': self.proof.binding_digest,
                    'reason_reference': 'synthetic-wire-withdrawal',
                },
                format='json',
            )
            self.assertEqual(result.status_code, 200)
            return encoded

        with patch.object(wire, 'encode_source_record', new=withdraw):
            self.assertIsNone(
                wire.read_work_document_content_json(self.owner, **arguments)
            )

    def test_native_metadata_constructor_revocation_stops_later_storage_io(self):
        """The unchanged byte reader reauthenticates after private metadata work."""
        from django.core.files.storage import default_storage

        from tasks import integration_s3_documents as documents

        original = documents._project_metadata
        opened = default_storage.open
        revoked = {'value': False}

        def revoke(*args):
            metadata = original(*args)
            self.owner_grant.delete()
            revoked['value'] = True
            return metadata

        def read(*args, **kwargs):
            self.assertFalse(
                revoked['value'], 'Storage must not open after constructor revocation'
            )
            return opened(*args, **kwargs)

        with (
            patch.object(documents, '_project_metadata', new=revoke),
            patch.object(default_storage, 'open', new=read),
        ):
            self.assertIsNone(
                documents.read_work_document_content(
                    self.owner,
                    environment=self.namespace.environment,
                    attachment_pk=self.attachment.pk,
                    max_bytes=128,
                )
            )

    def test_native_constructor_fault_after_revocation_is_opaque(self):
        """An invalid private result cannot disclose a source fault after revocation."""
        from tasks import integration_s3_documents as documents

        def fail(record):
            self.owner_grant.delete()
            raise ValueError('Private synthetic constructor detail')

        with patch.object(
            documents.DocumentContentSourceRecord, '__post_init__', new=fail
        ):
            self.assertIsNone(
                documents.read_work_document_content(
                    self.owner,
                    environment=self.namespace.environment,
                    attachment_pk=self.attachment.pk,
                    max_bytes=128,
                )
            )


class OriginalDownloadSchemaTests(SimpleTestCase):
    """The new route advertises a binary response and no source override fields."""

    def test_actual_route_schema_is_binary_get_without_write_operations(self):
        """Generate the actual view schema rather than a hand-written API example."""
        import importlib.util

        from django.conf import settings
        from django.urls import path

        from drf_spectacular.generators import SchemaGenerator

        # The reduced AI settings use DRF's default schema class. Load this
        # unchanged view source in a test-local module under the repository's
        # actual schema class; do not replace or reload any shared API module.
        with override_settings(
            SCHEMA_VENDOREXTENSION_LEVEL=0,
            REST_FRAMEWORK={
                **getattr(settings, 'REST_FRAMEWORK', {}),
                'DEFAULT_SCHEMA_CLASS': 'InvenTree.schema.ExtendedAutoSchema',
            },
        ):
            spec = importlib.util.spec_from_file_location(
                'qualified_original_schema', integration_content_api.__file__
            )
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            qualified = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(qualified)
            value = SchemaGenerator(
                patterns=[
                    path(
                        'integration-content/<int:pk>/',
                        qualified.IntegrationOriginalDownload.as_view(),
                    )
                ]
            ).get_schema(request=None, public=True)
        import json
        import os
        from pathlib import Path

        (Path(os.environ['TMPDIR']) / 'original-download-schema.json').write_text(
            json.dumps(value, indent=2) + '\n'
        )
        operation = value['paths']['/integration-content/{id}/']
        self.assertEqual(set(operation), {'get'})
        binary = operation['get']['responses']['200']['content'][
            'application/octet-stream'
        ]['schema']
        self.assertEqual(binary, {'type': 'string', 'format': 'binary'})
        self.assertFalse(
            any(
                item['name'] in {'environment', 'client_id', 'grant', 'source'}
                for item in operation['get'].get('parameters', [])
            )
        )
