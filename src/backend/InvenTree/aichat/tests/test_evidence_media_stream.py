"""R4: the authenticated, Range-aware evidence media streaming endpoint.

Playback seeks require HTTP 206; the DEBUG-only /media/ static serve emits
none, so evidence viewers ride this endpoint. These pins cover the auth
fence, both content types, the single-range grammar (start-end, open tail,
suffix), the 416 boundary, malformed-header fallback, and the guarantee
that the stored filename never leaves the server.
"""

import tempfile
from unittest import mock

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from common.models import Attachment
from part.models import Part

_MEDIA_ROOT = tempfile.mkdtemp(prefix='aimms-evidence-stream-test-')

#: Deterministic, position-identifiable body (4096 bytes, no filename bytes).
_BODY = bytes(range(256)) * 16
_STEM = 'wo104-seal-video'


def _make_attachment(name, content, part):
    """Create an attachment with background offloads suppressed."""
    with mock.patch('InvenTree.tasks.offload_task', return_value=True):
        return Attachment.objects.create(
            model_type='part',
            model_id=part.pk,
            attachment=SimpleUploadedFile(name, content),
            comment='evidence stream test',
        )


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

        cls.user = get_user_model().objects.create_user(
            username='evidence-viewer', password='pw'
        )
        cls.part = Part.objects.create(name='Seal Kit', description='seals')
        cls.video = _make_attachment(f'{_STEM}.mp4', _BODY, cls.part)
        cls.image = _make_attachment('nameplate-hx200.png', _BODY[:512], cls.part)

    def setUp(self):
        """Authenticated session by default; the anon test logs out."""
        self.client.force_login(self.user)

    def test_anonymous_request_is_rejected(self):
        """The endpoint is authenticated-only."""
        self.client.logout()
        response = self.client.get(_url(self.video.pk))
        self.assertIn(response.status_code, (401, 403))

    def test_missing_attachment_is_404(self):
        """No row: value-free 404."""
        response = self.client.get(_url(999999))
        self.assertEqual(response.status_code, 404)

    def test_row_with_storage_file_gone_is_404(self):
        """A dangling DB row (file deleted from storage) degrades to 404."""
        orphan = _make_attachment('gone-clip.mp4', _BODY, self.part)
        default_storage.delete(orphan.attachment.name)
        response = self.client.get(_url(orphan.pk))
        self.assertEqual(response.status_code, 404)

    def test_full_video_response(self):
        """No Range: 200, exact length, seekability advertised, mp4 type."""
        response = self.client.get(_url(self.video.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Length'], str(len(_BODY)))
        self.assertEqual(response['Accept-Ranges'], 'bytes')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
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
