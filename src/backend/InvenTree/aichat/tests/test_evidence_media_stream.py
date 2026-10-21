"""R4: the authenticated, Range-aware evidence media streaming endpoint.

Playback seeks require HTTP 206; the DEBUG-only /media/ static serve emits
none, so evidence viewers ride this endpoint. These pins cover the auth
fence, both content types, the single-range grammar (start-end, open tail,
suffix), the 416 boundary, malformed-header fallback, and the guarantee
that the stored filename never leaves the server.
"""

import hashlib
import tempfile
from unittest import mock

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from aichat.media_stream import EvidenceMediaStreamView
from aichat.models import (
    AttachmentIngest,
    AttachmentIngestPipeline,
    AttachmentIngestState,
)
from common.models import Attachment
from InvenTree.permissions import IsAuthenticatedOrReadScope
from part.models import Part

_MEDIA_ROOT = tempfile.mkdtemp(prefix='aimms-evidence-stream-test-')

#: Deterministic, position-identifiable body (4096 bytes, no filename bytes).
_BODY = bytes(range(256)) * 16
_STEM = 'wo104-seal-video'


def _make_attachment(name, content, part, *, pipeline=None):
    """Create an attachment with background offloads suppressed."""
    with mock.patch('InvenTree.tasks.offload_task', return_value=True):
        attachment = Attachment.objects.create(
            model_type='part',
            model_id=part.pk,
            attachment=SimpleUploadedFile(name, content),
            comment='evidence stream test',
        )
    if pipeline is not None:
        AttachmentIngest.objects.create(
            attachment_id=attachment.pk,
            model_type=attachment.model_type,
            model_id=attachment.model_id,
            source_sha256=hashlib.sha256(content).hexdigest(),
            pipeline=pipeline,
            state=AttachmentIngestState.INDEXED,
        )
    return attachment


def _url(attachment_id):
    """The evidence stream path for one attachment."""
    return f'/api/aichat/evidence/media/{attachment_id}/'


def _body(response):
    """Materialize a StreamingHttpResponse body."""
    return b''.join(response.streaming_content)


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class EvidenceMediaStreamTests(TestCase):
    """GET /api/aichat/evidence/media/<id>/ behaviour pins."""

    @classmethod
    def setUpTestData(cls):
        """One owner part, one video attachment, one image attachment."""
        from django.contrib.auth import get_user_model

        cls.user = get_user_model().objects.create_superuser(
            username='evidence-viewer', password='pw'
        )
        cls.part = Part.objects.create(name='Seal Kit', description='seals')
        cls.video = _make_attachment(
            f'{_STEM}.mp4', _BODY, cls.part, pipeline=AttachmentIngestPipeline.VIDEO
        )
        cls.image = _make_attachment(
            'nameplate-hx200.png',
            _BODY[:512],
            cls.part,
            pipeline=AttachmentIngestPipeline.IMAGE,
        )

    def setUp(self):
        """Authenticated session by default; the anon test logs out."""
        self.client.force_login(self.user)
        scope = mock.patch(
            'tasks.scope.client_codes_for_actor', return_value=frozenset({'internal'})
        )
        self.scopes = scope.start()
        self.addCleanup(scope.stop)

    def test_anonymous_request_is_rejected(self):
        """The endpoint is authenticated-only."""
        self.client.logout()
        response = self.client.get(_url(self.video.pk))
        self.assertIn(response.status_code, (401, 403))

    def test_permission_matches_attachment_read_scope(self):
        """OAuth tokens traverse the same read-scope permission as attachments."""
        self.assertEqual(
            EvidenceMediaStreamView.permission_classes, [IsAuthenticatedOrReadScope]
        )

    def test_missing_attachment_is_404(self):
        """No row: value-free 404."""
        response = self.client.get(_url(999999))
        self.assertEqual(response.status_code, 404)

    def test_row_with_storage_file_gone_is_404(self):
        """A dangling DB row (file deleted from storage) degrades to 404."""
        orphan = _make_attachment(
            'gone-clip.mp4', _BODY, self.part, pipeline=AttachmentIngestPipeline.VIDEO
        )
        default_storage.delete(orphan.attachment.name)
        response = self.client.get(_url(orphan.pk))
        self.assertEqual(response.status_code, 404)

    def test_generic_attachment_is_not_a_media_proxy(self):
        """An authenticated caller cannot stream an unindexed document here."""
        document = _make_attachment('service-report.pdf', b'%PDF-test', self.part)
        response = self.client.get(_url(document.pk))
        self.assertEqual(response.status_code, 404)

    def test_indexed_media_with_unsupported_suffix_is_404(self):
        """Only the closed image/video content-type allowlist is streamable."""
        renamed = _make_attachment(
            'recording.bin', _BODY, self.part, pipeline=AttachmentIngestPipeline.VIDEO
        )
        response = self.client.get(_url(renamed.pk))
        self.assertEqual(response.status_code, 404)

    def test_full_video_response(self):
        """No Range: 200, exact length, seekability advertised, mp4 type."""
        response = self.client.get(_url(self.video.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Length'], str(len(_BODY)))
        self.assertEqual(response['Accept-Ranges'], 'bytes')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['Content-Type'], 'video/mp4')
        self.assertEqual(_body(response), _BODY)

    def test_head_returns_playback_metadata_without_a_body(self):
        """Players can probe size, type, and range support before GET."""
        response = self.client.head(_url(self.video.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Length'], str(len(_BODY)))
        self.assertEqual(response['Accept-Ranges'], 'bytes')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['Content-Type'], 'video/mp4')
        self.assertEqual(response.content, b'')

    def test_head_honors_a_satisfiable_range(self):
        """A ranged HEAD advertises the same 206 coordinates as GET."""
        response = self.client.head(_url(self.video.pk), HTTP_RANGE='bytes=100-199')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response['Content-Range'], f'bytes 100-199/{len(_BODY)}')
        self.assertEqual(response['Content-Length'], '100')
        self.assertEqual(response.content, b'')

    def test_full_image_response_content_type(self):
        """A .png attachment streams as image/png."""
        response = self.client.get(_url(self.image.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(_body(response), _BODY[:512])

    def test_bounded_range_returns_exactly_the_requested_bytes(self):
        """bytes=0-1023: 206 with the first KiB and a correct Content-Range."""
        response = self.client.get(_url(self.video.pk), HTTP_RANGE='bytes=0-1023')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response['Content-Range'], f'bytes 0-1023/{len(_BODY)}')
        self.assertEqual(response['Content-Length'], '1024')
        self.assertEqual(_body(response), _BODY[:1024])

    def test_open_ended_range_returns_the_tail(self):
        """bytes=100-: 206 from offset 100 to EOF."""
        response = self.client.get(_url(self.video.pk), HTTP_RANGE='bytes=100-')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(
            response['Content-Range'], f'bytes 100-{len(_BODY) - 1}/{len(_BODY)}'
        )
        self.assertEqual(_body(response), _BODY[100:])

    def test_suffix_range_returns_the_last_bytes(self):
        """bytes=-100: 206 with exactly the last 100 bytes."""
        response = self.client.get(_url(self.video.pk), HTTP_RANGE='bytes=-100')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(
            response['Content-Range'],
            f'bytes {len(_BODY) - 100}-{len(_BODY) - 1}/{len(_BODY)}',
        )
        self.assertEqual(_body(response), _BODY[-100:])

    def test_out_of_bounds_range_is_416_with_the_size(self):
        """A start past EOF is unsatisfiable, not clamped."""
        response = self.client.get(_url(self.video.pk), HTTP_RANGE='bytes=999999999-')
        self.assertEqual(response.status_code, 416)
        self.assertEqual(response['Content-Range'], f'bytes */{len(_BODY)}')

    def test_malformed_range_is_ignored_not_rejected(self):
        """RFC 9110: an unparsable Range header yields the full 200."""
        response = self.client.get(_url(self.video.pk), HTTP_RANGE='bytes=abc')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_body(response), _BODY)

    def test_unbounded_integer_range_is_ignored_without_500(self):
        """Digit runs beyond the bounded grammar degrade to a full response."""
        response = self.client.get(
            _url(self.video.pk), HTTP_RANGE=f'bytes={"9" * 5000}-'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_body(response), _BODY)

    def test_stored_filename_never_leaves_the_server(self):
        """Neither headers nor body carry the uploader-chosen name."""
        stored_name = self.video.attachment.name
        self.assertIn(_STEM, stored_name)  # the check below is meaningful
        for response in (
            self.client.get(_url(self.video.pk)),
            self.client.get(_url(self.video.pk), HTTP_RANGE='bytes=0-1023'),
        ):
            for header, value in response.headers.items():
                self.assertNotIn(_STEM, str(value), header)
                self.assertNotIn(stored_name, str(value), header)
            self.assertNotIn(_STEM.encode(), _body(response))

    def test_revoked_scope_cannot_read_metadata_or_bytes(self):
        """A previously visible attachment becomes indistinguishable from absent."""
        self.scopes.return_value = frozenset()
        for suffix in ('', 'metadata/'):
            self.assertEqual(
                self.client.get(_url(self.image.pk) + suffix).status_code, 404
            )

    def test_revoked_role_is_read_fresh(self):
        """A session does not retain a former role grant."""
        self.user.is_superuser = False
        self.user.save(update_fields=['is_superuser'])
        self.assertEqual(self.client.get(_url(self.image.pk)).status_code, 404)

    def test_revision_is_pinned_and_metadata_is_private(self):
        """An absent revision never falls back silently to the current bytes."""
        url = _url(self.image.pk)
        response = self.client.get(url + 'metadata/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        revision = response.json()['revision']
        self.assertEqual(revision, hashlib.sha256(_BODY[:512]).hexdigest())
        self.assertEqual(self.client.get(url, {'revision': '0' * 64}).status_code, 404)
        self.assertEqual(
            _body(self.client.get(url, {'revision': revision})), _BODY[:512]
        )

    def test_bytes_changed_before_reindex_are_unavailable(self):
        """Storage drift cannot reuse the indexed revision's identity."""
        image = _make_attachment(
            'replaced-image.png',
            _BODY[:512],
            self.part,
            pipeline=AttachmentIngestPipeline.IMAGE,
        )
        with default_storage.open(image.attachment.name, 'wb') as handle:
            handle.write(b'changed bytes')
        self.assertEqual(self.client.get(_url(image.pk)).status_code, 404)

    def test_pdf_metadata_and_source(self):
        """Physical page count comes from the actual authorized PDF revision."""
        from io import BytesIO

        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        writer.add_blank_page(width=300, height=300)
        data = BytesIO()
        writer.write(data)
        pdf = _make_attachment(
            'source.pdf',
            data.getvalue(),
            self.part,
            pipeline=AttachmentIngestPipeline.DOC,
        )
        response = self.client.get(_url(pdf.pk) + 'metadata/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['page_count'], 2)
        self.assertEqual(
            self.client.get(_url(pdf.pk))['Content-Type'], 'application/pdf'
        )

    def test_markdown_metadata_and_source(self):
        """Both Markdown suffixes expose only verified DOC source bytes."""
        content = (
            b'# Service notes\n\n| Part | Status |\n| --- | --- |\n| Seal | OK |\n'
        )
        revision = hashlib.sha256(content).hexdigest()
        for suffix in ('.md', '.markdown', '.MD'):
            with self.subTest(suffix=suffix):
                attachment = _make_attachment(
                    f'notes{suffix}',
                    content,
                    self.part,
                    pipeline=AttachmentIngestPipeline.DOC,
                )
                url = _url(attachment.pk)
                metadata = self.client.get(url + 'metadata/', {'revision': revision})
                self.assertEqual(metadata.status_code, 200)
                self.assertEqual(metadata['Cache-Control'], 'private, no-store')
                self.assertEqual(
                    metadata.json(),
                    {
                        'revision': revision,
                        'content_type': 'text/markdown',
                        'page_count': None,
                        'page_labels': [],
                    },
                )
                response = self.client.get(url, {'revision': revision})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], 'text/markdown')
                self.assertEqual(response['Content-Length'], str(len(content)))
                self.assertEqual(response['ETag'], f'"{revision}"')
                self.assertEqual(response['Cache-Control'], 'private, no-store')
                self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(response['Accept-Ranges'], 'bytes')
                self.assertEqual(_body(response), content)
                for endpoint in (url, url + 'metadata/'):
                    stale = self.client.get(endpoint, {'revision': '0' * 64})
                    self.assertEqual(stale.status_code, 404)
                    self.assertEqual(stale.content, b'')

    def test_markdown_requires_indexed_doc_pipeline(self):
        """Unindexed and wrong-pipeline Markdown are value-free 404s."""
        attachment = _make_attachment('notes.md', b'# Notes', self.part)
        url = _url(attachment.pk)
        for endpoint in (url, url + 'metadata/'):
            response = self.client.get(endpoint)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.content, b'')
        ingest = AttachmentIngest.objects.create(
            attachment_id=attachment.pk,
            model_type=attachment.model_type,
            model_id=attachment.model_id,
            source_sha256=hashlib.sha256(b'# Notes').hexdigest(),
            pipeline=AttachmentIngestPipeline.DOC,
            state=AttachmentIngestState.INDEXED,
        )
        cases = [
            (AttachmentIngestPipeline.DOC, state)
            for state in AttachmentIngestState.values
            if state != AttachmentIngestState.INDEXED
        ] + [
            (pipeline, AttachmentIngestState.INDEXED)
            for pipeline in (
                AttachmentIngestPipeline.IMAGE,
                AttachmentIngestPipeline.VIDEO,
            )
        ]
        for pipeline, state in cases:
            with self.subTest(pipeline=pipeline, state=state):
                AttachmentIngest.objects.filter(pk=ingest.pk).update(
                    pipeline=pipeline, state=state
                )
                for endpoint in (url, url + 'metadata/'):
                    response = self.client.get(endpoint)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.content, b'')

    def test_markdown_range_and_head(self):
        """The preview gets one detection byte beyond its exact 512 KiB limit."""
        limit = 512 * 1024
        content = b'x' * (limit + 10)
        attachment = _make_attachment(
            'bounded.markdown',
            content,
            self.part,
            pipeline=AttachmentIngestPipeline.DOC,
        )
        url = _url(attachment.pk)
        revision = hashlib.sha256(content).hexdigest()
        for method in (self.client.get, self.client.head):
            response = method(
                url, {'revision': revision}, HTTP_RANGE=f'bytes=0-{limit}'
            )
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response['Content-Type'], 'text/markdown')
            self.assertEqual(
                response['Content-Range'], f'bytes 0-{limit}/{len(content)}'
            )
            self.assertEqual(response['Content-Length'], str(limit + 1))
            if response.streaming:
                self.assertEqual(_body(response), content[: limit + 1])
            else:
                self.assertEqual(response.content, b'')
        unsatisfiable = self.client.get(url, HTTP_RANGE=f'bytes={len(content)}-')
        self.assertEqual(unsatisfiable.status_code, 416)
        self.assertEqual(unsatisfiable['Content-Range'], f'bytes */{len(content)}')
        malformed = self.client.get(url, HTTP_RANGE='bytes=invalid')
        self.assertEqual(malformed.status_code, 200)
        self.assertEqual(_body(malformed), content)

    def test_markdown_changed_before_reindex_is_unavailable(self):
        """Neither metadata nor bytes can reuse a drifted Markdown revision."""
        attachment = _make_attachment(
            'replaced.md',
            b'# Original',
            self.part,
            pipeline=AttachmentIngestPipeline.DOC,
        )
        with default_storage.open(attachment.attachment.name, 'wb') as handle:
            handle.write(b'# Replaced')
        for suffix in ('', 'metadata/'):
            response = self.client.get(_url(attachment.pk) + suffix)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.content, b'')


@override_settings(
    MEDIA_ROOT=_MEDIA_ROOT,
    X_FRAME_OPTIONS='DENY',
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='ungranted-pdf-fixture',
)
class EvidencePDFFramePolicyTests(TestCase):
    """Cover the authorized successful-PDF SAMEORIGIN response policy."""

    _PDF_B64 = 'JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQo+PgplbmRvYmoKMiAwIG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDIKL0tpZHMgWyA0IDAgUiA2IDAgUiBdCj4+CmVuZG9iagozIDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdlcyAyIDAgUgo+PgplbmRvYmoKNCAwIG9iago8PAovVHlwZSAvUGFnZQovUmVzb3VyY2VzIDw8Ci9Gb250IDw8Ci9GMSA8PAovVHlwZSAvRm9udAovU3VidHlwZSAvVHlwZTEKL0Jhc2VGb250IC9IZWx2ZXRpY2EKPj4KPj4KPj4KL01lZGlhQm94IFsgMC4wIDAuMCAzMDAgMzAwIF0KL1BhcmVudCAyIDAgUgovQ29udGVudHMgNSAwIFIKPj4KZW5kb2JqCjUgMCBvYmoKPDwKL0xlbmd0aCA1Ngo+PgpzdHJlYW0KQlQgL0YxIDE2IFRmIDMwIDI1MCBUZCAoUERGIHBvbGljeSBmaXh0dXJlIHBhZ2UgMSkgVGogRVQKZW5kc3RyZWFtCmVuZG9iago2IDAgb2JqCjw8Ci9UeXBlIC9QYWdlCi9SZXNvdXJjZXMgPDwKL0ZvbnQgPDwKL0YxIDw8Ci9UeXBlIC9Gb250Ci9TdWJ0eXBlIC9UeXBlMQovQmFzZUZvbnQgL0hlbHZldGljYQo+Pgo+Pgo+PgovTWVkaWFCb3ggWyAwLjAgMC4wIDMwMCAzMDAgXQovUGFyZW50IDIgMCBSCi9Db250ZW50cyA3IDAgUgo+PgplbmRvYmoKNyAwIG9iago8PAovTGVuZ3RoIDU2Cj4+CnN0cmVhbQpCVCAvRjEgMTYgVGYgMzAgMjUwIFRkIChQREYgcG9saWN5IGZpeHR1cmUgcGFnZSAyKSBUaiBFVAplbmRzdHJlYW0KZW5kb2JqCnhyZWYKMCA4CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAxNSAwMDAwMCBuIAowMDAwMDAwMDU0IDAwMDAwIG4gCjAwMDAwMDAxMTkgMDAwMDAgbiAKMDAwMDAwMDE2OCAwMDAwMCBuIAowMDAwMDAwMzQ5IDAwMDAwIG4gCjAwMDAwMDA0NTUgMDAwMDAgbiAKMDAwMDAwMDYzNiAwMDAwMCBuIAp0cmFpbGVyCjw8Ci9TaXplIDgKL1Jvb3QgMyAwIFIKL0luZm8gMSAwIFIKPj4Kc3RhcnR4cmVmCjc0MgolJUVPRgo='

    @classmethod
    def setUpTestData(cls):
        """Persist the ordinary reader, grants, machines, and shared media fixtures."""
        from base64 import b64decode

        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group

        from assets.models import AssetMachine, Client, ClientScopeGrant
        from users.models import RuleSet

        cls.pdf_bytes = b64decode(cls._PDF_B64)
        cls.pdf_sha = hashlib.sha256(cls.pdf_bytes).hexdigest()
        cls.image_bytes = _BODY[:512]
        cls.video_bytes = _BODY
        cls.markdown_bytes = b'# Reference\n'

        user_model = get_user_model()
        cls.reader = user_model.objects.create_user(
            username='synthetic-pdf-policy-reader',
            is_active=True,
            is_staff=False,
            is_superuser=False,
            password=None,
        )
        cls.group = Group.objects.create(name='synthetic-pdf-policy-readers')
        cls.reader.groups.add(cls.group)
        RuleSet.objects.update_or_create(
            group=cls.group, name='work_order', defaults={'can_view': True}
        )
        cls.rule = RuleSet.objects.get(group=cls.group, name='work_order')

        cls.client_owned = Client.objects.create(
            code='synthetic-pdf-policy-owned', name='Synthetic PDF Policy Owned Client'
        )
        cls.client_spare = Client.objects.create(
            code='synthetic-pdf-policy-spare', name='Synthetic PDF Policy Spare Client'
        )
        cls.client_foreign = Client.objects.create(
            code='synthetic-pdf-policy-foreign',
            name='Synthetic PDF Policy Foreign Client',
        )
        cls.grant_owned = ClientScopeGrant.objects.create(
            user=cls.reader, client=cls.client_owned
        )
        cls.grant_spare = ClientScopeGrant.objects.create(
            user=cls.reader, client=cls.client_spare
        )

        cls.machine_owned = AssetMachine.objects.create(
            client=cls.client_owned, name='synthetic-pdf-policy-machine-owned'
        )
        cls.machine_foreign = AssetMachine.objects.create(
            client=cls.client_foreign, name='synthetic-pdf-policy-machine-foreign'
        )

        cls.pdf_owned = cls._make_native_attachment(
            cls.machine_owned,
            'synthetic-pdf-policy-owned.pdf',
            cls.pdf_bytes,
            AttachmentIngestPipeline.DOC,
        )
        cls.pdf_foreign = cls._make_native_attachment(
            cls.machine_foreign,
            'synthetic-pdf-policy-foreign.pdf',
            cls.pdf_bytes,
            AttachmentIngestPipeline.DOC,
        )
        cls.image_attachment = cls._make_native_attachment(
            cls.machine_owned,
            'synthetic-pdf-policy-control.png',
            cls.image_bytes,
            AttachmentIngestPipeline.IMAGE,
        )
        cls.video_attachment = cls._make_native_attachment(
            cls.machine_owned,
            'synthetic-pdf-policy-control.mp4',
            cls.video_bytes,
            AttachmentIngestPipeline.VIDEO,
        )
        cls.markdown_attachment = cls._make_native_attachment(
            cls.machine_owned,
            'synthetic-pdf-policy-control.md',
            cls.markdown_bytes,
            AttachmentIngestPipeline.DOC,
        )

    @classmethod
    def _make_native_attachment(cls, machine, name, content, pipeline):
        """Persist an assetmachine attachment with an indexed ingest row."""
        with mock.patch('InvenTree.tasks.offload_task', return_value=True):
            attachment = Attachment.objects.create(
                model_type='assetmachine',
                model_id=machine.pk,
                attachment=SimpleUploadedFile(name, content),
                comment='synthetic PDF policy fixture',
            )
            AttachmentIngest.objects.create(
                attachment_id=attachment.pk,
                model_type=attachment.model_type,
                model_id=attachment.model_id,
                source_sha256=hashlib.sha256(content).hexdigest(),
                pipeline=pipeline,
                state=AttachmentIngestState.INDEXED,
            )
        return attachment

    def setUp(self):
        """Verify native clickjacking middleware and log in the ordinary reader."""
        from django.conf import settings

        self.assertIn(
            'django.middleware.clickjacking.XFrameOptionsMiddleware',
            settings.MIDDLEWARE,
        )
        self.client.force_login(self.reader)

    def _assert_pdf_transport(
        self, response, *, length, content_type, etag, content_range
    ):
        """Assert the byte, cache, and security transport headers."""
        self.assertEqual(response['Accept-Ranges'], 'bytes')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        if length is not None:
            self.assertEqual(response['Content-Length'], str(length))
        if content_type is not None:
            self.assertEqual(response['Content-Type'], content_type)
        if etag is not None:
            self.assertEqual(response['ETag'], f'"{etag}"')
        if content_range is not None:
            self.assertEqual(response['Content-Range'], content_range)

    def _find_values(self, payload, keys):
        """Collect values stored under any of keys in nested JSON payloads."""
        found = []
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in keys:
                    found.append(value)
                found.extend(self._find_values(value, keys))
        elif isinstance(payload, list):
            for value in payload:
                found.extend(self._find_values(value, keys))
        return found

    def test_pdf_get_sameorigin(self):
        """Serve full PDF bytes with SAMEORIGIN framing."""
        response = self.client.get(_url(self.pdf_owned.pk))
        body = _body(response)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body, self.pdf_bytes)
        self._assert_pdf_transport(
            response,
            length=len(self.pdf_bytes),
            content_type='application/pdf',
            etag=self.pdf_sha,
            content_range=None,
        )
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')

    def test_pdf_range_get_sameorigin(self):
        """Serve an exact PDF byte range with SAMEORIGIN framing."""
        response = self.client.get(_url(self.pdf_owned.pk), HTTP_RANGE='bytes=20-39')
        body = _body(response)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(body), 20)
        self.assertEqual(body, self.pdf_bytes[20:40])
        self._assert_pdf_transport(
            response,
            length=20,
            content_type='application/pdf',
            etag=self.pdf_sha,
            content_range=f'bytes 20-39/{len(self.pdf_bytes)}',
        )
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')

    def test_pdf_head_sameorigin(self):
        """Report full PDF headers on HEAD with SAMEORIGIN framing."""
        response = self.client.head(_url(self.pdf_owned.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'')
        self._assert_pdf_transport(
            response,
            length=len(self.pdf_bytes),
            content_type='application/pdf',
            etag=self.pdf_sha,
            content_range=None,
        )
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')

    def test_pdf_range_head_sameorigin(self):
        """Report ranged PDF headers on HEAD with SAMEORIGIN framing."""
        response = self.client.head(_url(self.pdf_owned.pk), HTTP_RANGE='bytes=20-39')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b'')
        self._assert_pdf_transport(
            response,
            length=20,
            content_type='application/pdf',
            etag=self.pdf_sha,
            content_range=f'bytes 20-39/{len(self.pdf_bytes)}',
        )
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')

    def test_non_pdf_keeps_deny(self):
        """Keep default DENY framing for shared non-PDF media bytes."""
        markdown_types = ('text/markdown',)
        controls = (
            (self.image_attachment, self.image_bytes, 'image/png'),
            (self.video_attachment, self.video_bytes, 'video/mp4'),
            (self.markdown_attachment, self.markdown_bytes, None),
        )
        responses = []
        for attachment, expected_bytes, expected_type in controls:
            response = self.client.get(_url(attachment.pk))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(_body(response), expected_bytes)
            if expected_type is not None:
                self.assertEqual(response['Content-Type'], expected_type)
            else:
                self.assertIn(response['Content-Type'], markdown_types)
            responses.append(response)
        for response in responses:
            self.assertEqual(response['X-Frame-Options'], 'DENY')

    def test_pdf_metadata_keeps_deny(self):
        """Keep DENY framing on the PDF metadata JSON response."""
        response = self.client.get(f'{_url(self.pdf_owned.pk)}metadata/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('json', response['Content-Type'])
        payload = response.json()
        self.assertEqual(payload['page_count'], 2)
        self.assertEqual(payload['revision'], self.pdf_sha)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['X-Frame-Options'], 'DENY')

    def test_markdown_revoked_role_blocks_metadata_and_bytes(self):
        """Markdown authorization uses the fresh persisted role for both reads."""
        response = self.client.get(_url(self.markdown_attachment.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_body(response), self.markdown_bytes)
        self.rule.can_view = False
        self.rule.save()
        for suffix in ('', 'metadata/'):
            response = self.client.get(_url(self.markdown_attachment.pk) + suffix)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.content, b'')

    def test_markdown_revoked_client_blocks_metadata_and_bytes(self):
        """A remaining unrelated client grant cannot authorize Markdown reads."""
        response = self.client.get(_url(self.markdown_attachment.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_body(response), self.markdown_bytes)
        self.grant_owned.delete()
        for suffix in ('', 'metadata/'):
            response = self.client.get(_url(self.markdown_attachment.pk) + suffix)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.content, b'')

    def test_anonymous_keeps_denial(self):
        """Refuse anonymous PDF requests while keeping DENY framing."""
        self.client.logout()
        response = self.client.get(_url(self.pdf_owned.pk))
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response['X-Frame-Options'], 'DENY')

    def test_wrong_client_keeps_denial(self):
        """Refuse foreign-client PDF bytes while serving owned PDF bytes."""
        denied = self.client.get(_url(self.pdf_foreign.pk))
        self.assertEqual(denied.status_code, 404)
        response = self.client.get(_url(self.pdf_owned.pk))
        self.assertEqual(response.status_code, 200)
        body = _body(response)
        self.assertNotEqual(body, b'')
        self.assertEqual(body, self.pdf_bytes)
        self.assertEqual(denied['X-Frame-Options'], 'DENY')

    def test_revoked_role_keeps_denial(self):
        """Refuse PDF bytes once the persisted role loses its view right."""
        response = self.client.get(_url(self.pdf_owned.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_body(response), self.pdf_bytes)
        self.rule.can_view = False
        self.rule.save()
        denied = self.client.get(_url(self.pdf_owned.pk))
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied['X-Frame-Options'], 'DENY')

    def test_revoked_client_keeps_denial(self):
        """Refuse PDF bytes once the owned client grant is deleted."""
        from assets.models import ClientScopeGrant

        response = self.client.get(_url(self.pdf_owned.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_body(response), self.pdf_bytes)
        self.grant_owned.delete()
        self.assertTrue(
            ClientScopeGrant.objects.filter(pk=self.grant_spare.pk).exists()
        )
        denied = self.client.get(_url(self.pdf_owned.pk))
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied['X-Frame-Options'], 'DENY')

    def test_stale_revision_keeps_denial(self):
        """Refuse stale revision requests instead of substituting bytes."""
        response = self.client.get(_url(self.pdf_owned.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_body(response), self.pdf_bytes)
        denied = self.client.get(_url(self.pdf_owned.pk), {'revision': '0' * 64})
        self.assertEqual(denied.status_code, 404)
        self.assertNotEqual(denied.content, self.pdf_bytes)
        self.assertEqual(denied['X-Frame-Options'], 'DENY')

    def test_replaced_source_keeps_denial(self):
        """Refuse replaced stored bytes that no longer match the ingest digest."""
        attachment = self._make_native_attachment(
            self.machine_owned,
            'synthetic-pdf-policy-replaced.pdf',
            self.pdf_bytes,
            AttachmentIngestPipeline.DOC,
        )
        name = attachment.attachment.name
        with default_storage.open(name, 'wb') as handle:
            handle.write(b'replaced synthetic policy bytes')
        denied = self.client.get(_url(attachment.pk))
        self.assertEqual(denied.status_code, 404)
        response = self.client.get(_url(self.pdf_owned.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_body(response), self.pdf_bytes)
        self.assertEqual(denied['X-Frame-Options'], 'DENY')

    def test_missing_file_keeps_denial(self):
        """Refuse requests once the stored PDF file is deleted."""
        attachment = self._make_native_attachment(
            self.machine_owned,
            'synthetic-pdf-policy-missing.pdf',
            self.pdf_bytes,
            AttachmentIngestPipeline.DOC,
        )
        name = attachment.attachment.name
        default_storage.delete(name)
        denied = self.client.get(_url(attachment.pk))
        self.assertEqual(denied.status_code, 404)
        response = self.client.get(_url(self.pdf_owned.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_body(response), self.pdf_bytes)
        self.assertEqual(denied['X-Frame-Options'], 'DENY')

    def test_invalid_range_keeps_deny(self):
        """Keep DENY framing on unsatisfiable byte ranges."""
        response = self.client.get(_url(self.pdf_owned.pk), HTTP_RANGE='bytes=9999999-')
        self.assertEqual(response.status_code, 416)
        self._assert_pdf_transport(
            response,
            length=None,
            content_type=None,
            etag=None,
            content_range=f'bytes */{len(self.pdf_bytes)}',
        )
        self.assertEqual(response['X-Frame-Options'], 'DENY')

    def test_unrelated_response_keeps_deny(self):
        """Keep DENY framing on the unrelated live API root response."""
        response = self.client.get('/api/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Frame-Options'], 'DENY')
