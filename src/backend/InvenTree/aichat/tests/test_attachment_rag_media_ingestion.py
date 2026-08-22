"""R3/R4 media RAG: router matrix, image + video paths, supersede, heal, purge."""

import contextlib
import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import PurePath
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from tasks.models import WorkOrder
from tasks.procedure_models import (
    Procedure,
    ProcedureRevision,
    WorkOrderProcedureApplication,
    WorkOrderStepExecution,
)

from aichat.models import (
    AttachmentChunk,
    AttachmentIngest,
    AttachmentIngestState,
    MediaSegment,
)
from aichat.services.attachment_ingestion import (
    FLAG_DEPENDENT_SKIPS,
    AttachmentIngestionError,
    _media_owner_coordinates,
    _video_source,
    derive_client_codes,
    heal_media_thumbnails,
    media_search_document_id,
    purge_attachment_artifacts,
    restamp_machine_client_codes,
    restamp_work_order_media_client_codes,
    route_attachment,
    run_ingest,
)
from common.models import Attachment
from company.models import Company

from .test_attachment_rag_ingestion import (
    _MD,
    FakeProjection,
    RagFixtureTestCase,
    _ai_settings,
    _make_attachment,
)

_PNG = b'\x89PNG\r\n\x1a\n' + b'IHDR-evidence-alpha' + b'\x00' * 48
_PNG_ALT = b'\x89PNG\r\n\x1a\n' + b'IHDR-evidence-beta' + b'\x00' * 48
_JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 64
_WEBP = b'RIFF\x00\x00\x00\x00WEBPVP8 ' + b'\x00' * 64
_GIF = b'GIF89a' + b'\x00' * 64
_BMP = b'BM' + b'\x00' * 64
_MP4 = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 64
_MP4_ALT = b'\x00\x00\x00\x18ftypmp42' + b'take-two-rev' + b'\x00' * 52

#: Sentinels the fake ffmpeg tools write, so keyframe/clip plumbing is provable.
_CLIP_SENTINEL = b'CLIP-SENTINEL-BYTES-0000'
_FRAME_SENTINEL = b'\xff\xd8\xff\xe0KEYFRAME-SENTINEL'

#: The container creation_time the fake probe reports.
_RECORDED_AT = datetime(2026, 3, 14, 10, 30, tzinfo=UTC)

#: The nominal 130 s plan at the 60/5 defaults.
_EXPECTED_WINDOWS = [(0.0, 60.0), (55.0, 115.0), (110.0, 130.0)]

_MEDIA_OWNERS = ('workorder', 'workorderstepexecution', 'assetmachine')

#: The §5.2 media document field set; a drifted projection is a broken index.
_MEDIA_DOC_KEYS = {
    'id',
    'attachment_id',
    'source_sha256',
    'media_type',
    'model_type',
    'model_id',
    'work_order_id',
    'step_execution_id',
    'asset_id',
    'machine_name',
    'client_codes',
    'scope_key',
    'access_class',
    'is_current',
    'timecode_start_s',
    'timecode_end_s',
    'duration_s',
    'segment_index',
    'segment_count',
    'caption',
    'ocr_text',
    'transcript',
    'thumbnail_path',
    'source_file_name',
    'recorded_at',
    'uploaded_at',
    'indexed_at',
    'media_vector',
    'embedding_model',
    'embedding_dimensions',
}


def _media_settings(**overrides):
    """Valid media-RAG AI configuration on top of the doc-RAG baseline."""
    values = {
        'FEATURE_MEDIA_RAG_INGEST': True,
        'GCP_PROJECT_ID': 'proj',
        'GCP_LOCATION': 'us-central1',
        'GCP_CREDENTIALS_PATH': '/tmp/wif.json',
        # The image path hard-depends on gpt-4o captions; the validator
        # fails closed without the endpoint (review finding, R3).
        'AZURE_OPENAI_ENDPOINT': 'https://openai.example',
    }
    values.update(overrides)
    return _ai_settings(**values)


class FakeGeminiClient:
    """Deterministic media-space vectors; records calls for assertions."""

    model = 'fake-gemini'
    dimensions = 3072

    def __init__(self):
        """Initialize recorders."""
        self.image_calls = []
        self.video_calls = []
        self.query_calls = 0

    def embed_image(self, data, *, mime_type):
        """Return one fixed-width image vector and record the call."""
        self.image_calls.append((len(data), mime_type))
        return [0.25] * self.dimensions

    def embed_video_segment(self, data, *, mime_type):
        """Return one fixed-width clip vector and record the call (R4)."""
        self.video_calls.append((len(data), mime_type))
        return [0.25] * self.dimensions

    def embed_query(self, text):
        """Return one query vector and count the call (retrieval side)."""
        self.query_calls += 1
        return [0.25] * self.dimensions


class FakeMediaProjection(FakeProjection):
    """Media-index recorder: the docs fake plus the thumbnail heal merge."""

    index_name = 'aimms-media-evidence-v1'

    def merge_thumbnail(self, *, search_doc_id, thumbnail_path):
        """Record a thumbnail-reference merge."""
        self.operations.append(('merge_thumbnail', search_doc_id, thumbnail_path))
        return 1

    def merge_media_metadata(self, *, attachment_id, fields):
        """Record a scope/coordinate merge; pretend one document changed."""
        frozen = tuple(
            sorted(
                (key, tuple(value) if isinstance(value, list) else value)
                for key, value in fields.items()
            )
        )
        self.operations.append(('merge_metadata', attachment_id, frozen))
        return 1


@contextlib.contextmanager
def _image_providers(ocr='Nameplate SN-100 480V', caption='Motor nameplate photo'):
    """Fake the DI read client and the captioner for one image ingest."""
    fake_di = mock.Mock(
        analyze_read_text=mock.Mock(return_value=SimpleNamespace(content=f' {ocr} '))
    )
    with (
        mock.patch(
            'ai.core.integrations.doc_intelligence.get_doc_intelligence_client',
            return_value=fake_di,
        ),
        mock.patch(
            'ai.core.integrations.image_caption.caption_image', return_value=caption
        ) as caption_mock,
    ):
        yield fake_di, caption_mock


@contextlib.contextmanager
def _video_tool_fakes(
    *, duration_s=130.0, recorded_at=_RECORDED_AT, has_video_stream=True
):
    """Fake the local ffmpeg toolchain for one video ingest.

    Yields:
        dict: the recorded ``cut``/``keyframe`` call arguments.
    """
    from aichat.services.video_tools import VideoProbe

    probe = VideoProbe(
        duration_s=duration_s,
        recorded_at=recorded_at,
        width=1280,
        height=720,
        has_video_stream=has_video_stream,
    )
    calls = {'cut': [], 'keyframe': [], 'clip_suffixes': []}

    def fake_cut(path, start_s, duration, out_path):
        calls['cut'].append((start_s, duration))
        calls['clip_suffixes'].append(PurePath(out_path).suffix)
        with open(out_path, 'wb') as handle:
            handle.write(_CLIP_SENTINEL)

    def fake_keyframe(path, at_s, out_path):
        calls['keyframe'].append(at_s)
        with open(out_path, 'wb') as handle:
            handle.write(_FRAME_SENTINEL)

    with (
        mock.patch('aichat.services.video_tools.probe_video', return_value=probe),
        mock.patch('aichat.services.video_tools.cut_segment', side_effect=fake_cut),
        mock.patch(
            'aichat.services.video_tools.extract_keyframe', side_effect=fake_keyframe
        ),
    ):
        yield calls


class MediaFixtureTestCase(RagFixtureTestCase):
    """RAG fixtures plus the media owners: work orders and a step execution."""

    @classmethod
    def setUpTestData(cls):
        """Add WOs (client, customer-attributed, orphan) and a step chain."""
        super().setUpTestData()
        cls.author = get_user_model().objects.create_user(
            username='media-author', password='x'
        )
        cls.customer = Company.objects.create(name='Cust Co', is_customer=True)
        cls.work_order = WorkOrder.objects.create(
            title='Press 1 service',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=cls.machine,
        )
        cls.work_order_customer = WorkOrder.objects.create(
            title='Customer job',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=cls.machine,
            customer=cls.customer,
        )
        cls.work_order_orphan = WorkOrder.objects.create(
            title='Orphan job',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=cls.machine_unscoped,
        )
        cls.work_order_bare = WorkOrder.objects.create(
            title='Machineless job',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
        )
        procedure = Procedure.objects.create(
            code='MED-P', name='Media proc', created_by=cls.author
        )
        revision = ProcedureRevision.objects.create(
            procedure=procedure,
            revision=1,
            work_order_type='corrective',
            created_by=cls.author,
        )
        application = WorkOrderProcedureApplication.objects.create(
            work_order=cls.work_order,
            revision=revision,
            snapshot={},
            snapshot_hash='0' * 64,
            applied_by=cls.author,
            idempotency_key='media-tests',
        )
        cls.step = WorkOrderStepExecution.objects.create(
            application=application, step_key=uuid.uuid4(), sequence=1, step_snapshot={}
        )
        application_customer = WorkOrderProcedureApplication.objects.create(
            work_order=cls.work_order_customer,
            revision=revision,
            snapshot={},
            snapshot_hash='0' * 64,
            applied_by=cls.author,
            idempotency_key='media-tests-customer',
        )
        cls.step_customer = WorkOrderStepExecution.objects.create(
            application=application_customer,
            step_key=uuid.uuid4(),
            sequence=1,
            step_snapshot={},
        )

    def _run_media(self, attachment_id, *, settings_overrides=None, **kwargs):
        """run_ingest with both Django co-gates lit, media config, and fakes."""
        embedder = kwargs.pop('media_embedding_client', FakeGeminiClient())
        media_projection = kwargs.pop('media_projection', FakeMediaProjection())
        ai_settings = _media_settings(**(settings_overrides or {}))
        with (
            override_settings(
                AIMMS_ATTACHMENT_RAG_ENABLED=True, AIMMS_MEDIA_RAG_ENABLED=True
            ),
            mock.patch('ai.core.config.get_settings', return_value=ai_settings),
        ):
            row = run_ingest(
                attachment_id,
                media_embedding_client=embedder,
                media_projection=media_projection,
                **kwargs,
            )
        return row, embedder, media_projection


class MediaRouterTests(MediaFixtureTestCase):
    """The R3 router arm: owner x kind x flag-conjunction matrix."""

    def _route(self, attachment, head, *, media_on=True, django_on=True):
        """Route one attachment under an explicit flag combination."""
        ai_settings = _media_settings() if media_on else _ai_settings()
        with (
            override_settings(AIMMS_MEDIA_RAG_ENABLED=django_on),
            mock.patch('ai.core.config.get_settings', return_value=ai_settings),
        ):
            return route_attachment(attachment, head)

    def test_supported_rasters_ingest_only_under_full_conjunction(self):
        """PNG/JPEG/WEBP on every media owner ingest iff BOTH flags are on."""
        for model_type in _MEDIA_OWNERS:
            attachment = _make_attachment(model_type, 4300, 'shot.png', _PNG)
            for head in (_PNG, _JPEG, _WEBP):
                with self.subTest(model_type=model_type, magic=head[:4]):
                    decision = self._route(attachment, head)
                    self.assertEqual(
                        (decision.action, decision.pipeline), ('ingest', 'image')
                    )
                    for media_on, django_on in ((True, False), (False, True)):
                        dark = self._route(
                            attachment, head, media_on=media_on, django_on=django_on
                        )
                        self.assertEqual(dark.action, 'skip')
                        self.assertEqual(dark.pipeline, 'image')
                        self.assertEqual(
                            dark.reason, 'ATTACHMENT_SKIP_MEDIA_PIPELINE_DARK'
                        )

    def test_gif_and_bmp_are_terminal_unsupported(self):
        """Non-embeddable rasters skip terminally even with both flags on."""
        for model_type in _MEDIA_OWNERS:
            attachment = _make_attachment(model_type, 4301, 'anim.gif', _GIF)
            for head in (_GIF, _BMP):
                with self.subTest(model_type=model_type, magic=head[:4]):
                    decision = self._route(attachment, head)
                    self.assertEqual(decision.action, 'skip')
                    self.assertEqual(decision.pipeline, 'image')
                    self.assertEqual(
                        decision.reason, 'ATTACHMENT_SKIP_UNSUPPORTED_TYPE'
                    )
        # Terminal means terminal: no flag flip may ever revive these stamps.
        self.assertNotIn('ATTACHMENT_SKIP_UNSUPPORTED_TYPE', FLAG_DEPENDENT_SKIPS)

    def test_media_owner_video_ingests_only_under_full_conjunction(self):
        """MP4 on every media owner ingests iff BOTH flags are on (R4)."""
        for model_type in _MEDIA_OWNERS:
            attachment = _make_attachment(model_type, 4302, 'clip.mp4', _MP4)
            with self.subTest(model_type=model_type):
                decision = self._route(attachment, _MP4)
                self.assertEqual(
                    (decision.action, decision.pipeline), ('ingest', 'video')
                )
                for media_on, django_on in ((True, False), (False, True)):
                    dark = self._route(
                        attachment, _MP4, media_on=media_on, django_on=django_on
                    )
                    self.assertEqual(dark.action, 'skip')
                    self.assertEqual(dark.pipeline, 'video')
                    self.assertEqual(dark.reason, 'ATTACHMENT_SKIP_VIDEO_PIPELINE_DARK')

    def test_part_image_stays_excluded_with_flags_on(self):
        """Part imagery keeps its decision-#10 skip regardless of media flags."""
        attachment = _make_attachment('part', self.part.pk, 'photo.png', _PNG)
        decision = self._route(attachment, _PNG)
        self.assertEqual(decision.action, 'skip')
        self.assertEqual(decision.pipeline, 'image')
        self.assertEqual(decision.reason, 'ATTACHMENT_SKIP_PART_IMAGE')

    def test_gif_machine_upload_records_skip_row(self):
        """The terminal unsupported skip is recorded on a registry row."""
        attachment = _make_attachment('assetmachine', self.machine.pk, 'anim.gif', _GIF)
        row, _embedder, _projection = self._run_media(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.SKIPPED)
        self.assertEqual(row.error_code, 'ATTACHMENT_SKIP_UNSUPPORTED_TYPE')
        self.assertEqual(row.pipeline, 'image')
        attachment.refresh_from_db()
        stamp = attachment.metadata['ai_ingest']
        self.assertEqual(stamp['state'], 'skipped')
        self.assertEqual(stamp['reason'], 'ATTACHMENT_SKIP_UNSUPPORTED_TYPE')


class MediaImageIngestTests(MediaFixtureTestCase):
    """The image happy path, its failure legs, and photo-photo supersede."""

    def test_workorder_photo_end_to_end(self):
        """A WO photo lands indexed with the full §5.2 media document."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'nameplate.png', _PNG
        )
        Attachment.objects.filter(pk=attachment.pk).update(
            thumbnail='attachments/thumbs/nameplate.thumb.png'
        )
        with _image_providers() as (fake_di, _caption):
            row, embedder, projection = self._run_media(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)
        self.assertEqual(row.pipeline, 'image')
        self.assertEqual(row.extractor, 'di_read')
        self.assertEqual(row.chunk_count, 0)
        self.assertEqual(row.segment_count, 1)
        self.assertEqual(row.embedding_model, 'fake-gemini')
        self.assertEqual(row.embedding_dimensions, 3072)
        self.assertEqual(row.search_index_name, 'aimms-media-evidence-v1')
        self.assertEqual(row.client_codes, ['acme'])
        self.assertEqual(embedder.image_calls, [(len(_PNG), 'image/png')])
        self.assertEqual(
            fake_di.analyze_read_text.call_args.kwargs['content_type'], 'image/png'
        )
        self.assertEqual([op[0] for op in projection.operations], ['upsert'])

        segments = list(MediaSegment.objects.filter(ingest=row))
        self.assertEqual(len(segments), 1)
        segment = segments[0]
        self.assertEqual(segment.media_type, 'image')
        self.assertEqual(segment.segment_index, 0)
        self.assertEqual(segment.caption, 'Motor nameplate photo')
        self.assertEqual(segment.ocr_text, 'Nameplate SN-100 480V')
        self.assertEqual(
            segment.thumbnail_path, 'attachments/thumbs/nameplate.thumb.png'
        )
        expected_id = media_search_document_id(attachment.pk, row.source_sha256)
        self.assertEqual(segment.search_doc_id, expected_id)

        doc = projection.documents[0]
        self.assertEqual(set(doc), _MEDIA_DOC_KEYS)
        self.assertEqual(doc['id'], f'att-{attachment.pk}-{row.source_sha256[:12]}-img')
        self.assertEqual(doc['media_type'], 'image')
        self.assertEqual(doc['model_type'], 'workorder')
        self.assertEqual(doc['model_id'], self.work_order.pk)
        self.assertEqual(doc['work_order_id'], self.work_order.pk)
        self.assertIsNone(doc['step_execution_id'])
        self.assertEqual(doc['asset_id'], 'SN-100')
        self.assertEqual(doc['machine_name'], 'Press 1')
        self.assertEqual(doc['client_codes'], ['acme'])
        self.assertEqual(doc['scope_key'], 'site-a')
        self.assertEqual(doc['access_class'], 'evidence_recording')
        self.assertTrue(doc['is_current'])
        self.assertIsNone(doc['timecode_start_s'])
        self.assertIsNone(doc['timecode_end_s'])
        self.assertIsNone(doc['duration_s'])
        self.assertEqual(doc['segment_index'], 0)
        self.assertEqual(doc['segment_count'], 1)
        self.assertEqual(doc['caption'], 'Motor nameplate photo')
        self.assertEqual(doc['ocr_text'], 'Nameplate SN-100 480V')
        self.assertEqual(doc['transcript'], '')
        self.assertEqual(
            doc['thumbnail_path'], 'attachments/thumbs/nameplate.thumb.png'
        )
        self.assertEqual(doc['source_file_name'], 'nameplate.png')
        self.assertIsNone(doc['recorded_at'])  # header-only PNG has no EXIF
        self.assertTrue(doc['uploaded_at'])
        self.assertTrue(doc['indexed_at'])
        self.assertEqual(len(doc['media_vector']), 3072)
        self.assertEqual(doc['embedding_model'], 'fake-gemini')
        self.assertEqual(doc['embedding_dimensions'], 3072)

        attachment.refresh_from_db()
        stamp = attachment.metadata['ai_ingest']
        self.assertEqual(stamp['state'], 'indexed')
        self.assertEqual(stamp['sha'], row.source_sha256)

    def test_step_photo_carries_step_and_work_order_ids(self):
        """A step-execution photo stamps both coordinates and the WO scope."""
        attachment = _make_attachment(
            'workorderstepexecution', self.step.pk, 'step-check.png', _PNG
        )
        with _image_providers():
            row, _embedder, projection = self._run_media(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)
        self.assertEqual(row.client_codes, ['acme'])
        doc = projection.documents[0]
        self.assertEqual(doc['model_type'], 'workorderstepexecution')
        self.assertEqual(doc['step_execution_id'], self.step.pk)
        self.assertEqual(doc['work_order_id'], self.work_order.pk)
        self.assertEqual(doc['asset_id'], 'SN-100')
        self.assertEqual(doc['access_class'], 'evidence_recording')

    def test_image_oversize_records_image_skip(self):
        """The image cap binds with its own code and no provider work."""
        attachment = _make_attachment('workorder', self.work_order.pk, 'huge.png', _PNG)
        Attachment.objects.filter(pk=attachment.pk).update(file_size=2 * 1024 * 1024)
        with mock.patch(
            'aichat.services.attachment_ingestion.extract_image_text',
            side_effect=AssertionError('no OCR for an oversize skip'),
        ):
            row, embedder, _projection = self._run_media(
                attachment.pk, settings_overrides={'RAG_MAX_IMAGE_MB': 1}
            )
        self.assertEqual(row.state, AttachmentIngestState.SKIPPED)
        self.assertEqual(row.error_code, 'ATTACHMENT_SKIP_IMAGE_OVERSIZE')
        self.assertEqual(row.pipeline, 'image')
        self.assertEqual(embedder.image_calls, [])

    def test_ocr_failure_fails_closed_and_value_free(self):
        """A DI read failure fails the ingest; provider text never logs."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'blurry.png', _PNG
        )
        failing = mock.Mock(
            analyze_read_text=mock.Mock(
                side_effect=RuntimeError('secret-token-XYZZY in provider error')
            )
        )
        with (
            mock.patch(
                'ai.core.integrations.doc_intelligence.get_doc_intelligence_client',
                return_value=failing,
            ),
            mock.patch(
                'ai.core.integrations.image_caption.caption_image',
                side_effect=AssertionError('caption must not run after OCR failure'),
            ),
            self.assertLogs('inventree', level='WARNING') as captured,
            self.assertRaises(AttachmentIngestionError) as caught,
        ):
            self._run_media(attachment.pk)
        self.assertEqual(caught.exception.code, 'ATTACHMENT_EXTRACTION_FAILED')
        rendered = '\n'.join(captured.output)
        self.assertIn('Document Intelligence OCR failed', rendered)
        self.assertNotIn('secret-token-XYZZY', rendered)
        row = AttachmentIngest.objects.get(attachment_id=attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.FAILED)
        self.assertEqual(row.error_code, 'ATTACHMENT_EXTRACTION_FAILED')

    def test_unconfigured_di_fails_unavailable(self):
        """No DI client means EXTRACTION_UNAVAILABLE, never a silent skip."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'noconf.png', _PNG
        )
        with (
            mock.patch(
                'ai.core.integrations.doc_intelligence.get_doc_intelligence_client',
                return_value=None,
            ),
            self.assertRaises(AttachmentIngestionError) as caught,
        ):
            self._run_media(attachment.pk)
        self.assertEqual(caught.exception.code, 'ATTACHMENT_EXTRACTION_UNAVAILABLE')
        row = AttachmentIngest.objects.get(attachment_id=attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.FAILED)

    def test_caption_failure_fails_with_caption_code(self):
        """An ImageCaptionError fails the row under the caption's own code."""
        from ai.core.integrations.image_caption import ImageCaptionError

        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'nocap.png', _PNG
        )
        fake_di = mock.Mock(
            analyze_read_text=mock.Mock(return_value=SimpleNamespace(content='text'))
        )
        with (
            mock.patch(
                'ai.core.integrations.doc_intelligence.get_doc_intelligence_client',
                return_value=fake_di,
            ),
            mock.patch(
                'ai.core.integrations.image_caption.caption_image',
                side_effect=ImageCaptionError('caption failed'),
            ),
            self.assertRaises(AttachmentIngestionError) as caught,
        ):
            self._run_media(attachment.pk)
        self.assertEqual(caught.exception.code, 'ATTACHMENT_CAPTION_FAILED')
        row = AttachmentIngest.objects.get(attachment_id=attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.FAILED)
        self.assertEqual(row.error_code, 'ATTACHMENT_CAPTION_FAILED')

    def test_empty_ocr_text_still_indexes(self):
        """A photo with no legible text is a legitimate indexed outcome."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'plain.png', _PNG
        )
        with _image_providers(ocr=''):
            row, _embedder, projection = self._run_media(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)
        doc = projection.documents[0]
        self.assertEqual(doc['ocr_text'], '')
        self.assertEqual(doc['caption'], 'Motor nameplate photo')
        segment = MediaSegment.objects.get(ingest=row)
        self.assertEqual(segment.ocr_text, '')

    def test_photo_reupload_supersedes_old_sha_in_media_projection(self):
        """A replaced photo purges the old sha from the MEDIA index, zero-gap."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'rev-a.png', _PNG
        )
        with _image_providers():
            first, _e, _p = self._run_media(attachment.pk)
        self.assertEqual(first.state, AttachmentIngestState.INDEXED)
        with mock.patch('InvenTree.tasks.offload_task', return_value=True):
            attachment.attachment.save(
                'rev-b.png', SimpleUploadedFile('rev-b.png', _PNG_ALT)
            )
        with _image_providers():
            second, _e2, projection2 = self._run_media(attachment.pk)
        self.assertNotEqual(second.pk, first.pk)
        self.assertEqual(second.state, AttachmentIngestState.INDEXED)
        first.refresh_from_db()
        self.assertEqual(first.state, AttachmentIngestState.SUPERSEDED)
        self.assertEqual(
            projection2.operations,
            [
                ('upsert', 1),
                ('mark_stale', attachment.pk, first.source_sha256),
                ('purge_sha', attachment.pk, first.source_sha256),
            ],
        )


class MediaVideoIngestTests(MediaFixtureTestCase):
    """The R4 video path: segmentation E2E, in-run skips, failure, cleanup."""

    def test_video_source_does_not_swallow_body_not_implemented(self):
        """Only storage.path may trigger spill fallback, never the with-body."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'body-error.mp4', _MP4
        )
        with self.assertRaisesRegex(NotImplementedError, 'body sentinel'):
            with _video_source(attachment):
                raise NotImplementedError('body sentinel')

    def test_sniffed_brand_controls_clip_container_not_uploader_extension(self):
        """Renamed MP4/MOV files emit clips matching their verified MIME."""
        cases = (
            ('renamed-evidence.bin', _MP4, '.mp4', 'video/mp4'),
            (
                'misnamed-evidence.mp4',
                b'\x00\x00\x00\x18ftypqt  ' + b'\x00' * 64,
                '.mov',
                'video/quicktime',
            ),
        )
        for name, content, expected_suffix, expected_mime in cases:
            with self.subTest(name=name):
                attachment = _make_attachment(
                    'workorder', self.work_order.pk, name, content
                )
                with _image_providers(), _video_tool_fakes() as calls:
                    row, embedder, _projection = self._run_media(attachment.pk)
                self.assertEqual(row.state, AttachmentIngestState.INDEXED)
                self.assertEqual(calls['clip_suffixes'], [expected_suffix] * 3)
                self.assertEqual(
                    embedder.video_calls, [(len(_CLIP_SENTINEL), expected_mime)] * 3
                )

    def test_workorder_video_multi_segment_end_to_end(self):
        """A 130 s WO recording lands as three segments with full doc pins."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'seal-swap.mp4', _MP4
        )
        sha = hashlib.sha256(_MP4).hexdigest()
        sha12 = sha[:12]
        with (
            _image_providers(
                ocr='SEAL REPLACEMENT', caption='Seal replacement recording'
            ),
            _video_tool_fakes() as calls,
        ):
            row, embedder, projection = self._run_media(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)
        self.assertEqual(row.pipeline, 'video')
        self.assertEqual(row.extractor, 'ffmpeg')
        self.assertEqual(row.chunk_count, 0)
        self.assertEqual(row.segment_count, 3)
        self.assertEqual(row.source_sha256, sha)
        self.assertEqual(row.embedding_model, 'fake-gemini')
        self.assertEqual(row.embedding_dimensions, 3072)
        self.assertEqual(row.search_index_name, 'aimms-media-evidence-v1')
        self.assertEqual(row.client_codes, ['acme'])
        # ffmpeg saw the NOMINAL plan; keyframes sit at each clip midpoint.
        self.assertEqual(calls['cut'], [(0.0, 60.0), (55.0, 60.0), (110.0, 20.0)])
        self.assertEqual(calls['keyframe'], [30.0, 30.0, 10.0])
        self.assertEqual(embedder.video_calls, [(len(_CLIP_SENTINEL), 'video/mp4')] * 3)
        self.assertEqual(embedder.image_calls, [])
        self.assertEqual(projection.operations, [('upsert', 3)])

        segments = list(
            MediaSegment.objects.filter(ingest=row).order_by('segment_index')
        )
        self.assertEqual(len(segments), 3)
        for index, (segment, (start, end)) in enumerate(
            zip(segments, _EXPECTED_WINDOWS, strict=True)
        ):
            self.assertEqual(segment.media_type, 'video_segment')
            self.assertEqual(segment.segment_index, index)
            self.assertEqual(segment.timecode_start_s, start)
            self.assertEqual(segment.timecode_end_s, end)
            self.assertEqual(segment.caption, 'Seal replacement recording')
            self.assertEqual(segment.ocr_text, 'SEAL REPLACEMENT')
            self.assertEqual(
                segment.search_doc_id, f'att-{attachment.pk}-{sha12}-s{index}'
            )
            self.assertEqual(
                segment.search_doc_id,
                media_search_document_id(attachment.pk, sha, segment_index=index),
            )
            self.assertEqual(
                segment.thumbnail_path,
                f'ai/keyframes/{attachment.pk}/{sha12}-s{index}.jpg',
            )
            # The keyframe FILE exists under MEDIA_ROOT with the fake's bytes.
            with default_storage.open(segment.thumbnail_path) as handle:
                self.assertEqual(handle.read(), _FRAME_SENTINEL)

        docs = projection.documents
        self.assertEqual(len(docs), 3)
        self.assertEqual(len(_MEDIA_DOC_KEYS), 30)
        for index, doc in enumerate(docs):
            start, end = _EXPECTED_WINDOWS[index]
            self.assertEqual(set(doc), _MEDIA_DOC_KEYS)
            self.assertEqual(doc['id'], f'att-{attachment.pk}-{sha12}-s{index}')
            self.assertEqual(doc['media_type'], 'video_segment')
            self.assertEqual(doc['model_type'], 'workorder')
            self.assertEqual(doc['work_order_id'], self.work_order.pk)
            self.assertIsNone(doc['step_execution_id'])
            self.assertEqual(doc['access_class'], 'evidence_recording')
            self.assertEqual(doc['client_codes'], ['acme'])
            self.assertEqual(doc['scope_key'], 'site-a')
            self.assertTrue(doc['is_current'])
            self.assertEqual(doc['timecode_start_s'], start)
            self.assertEqual(doc['timecode_end_s'], end)
            self.assertEqual(doc['duration_s'], round(end - start, 3))
            self.assertEqual(doc['segment_index'], index)
            self.assertEqual(doc['segment_count'], 3)
            self.assertEqual(doc['caption'], 'Seal replacement recording')
            self.assertEqual(doc['ocr_text'], 'SEAL REPLACEMENT')
            self.assertEqual(doc['transcript'], '')
            self.assertEqual(
                doc['thumbnail_path'],
                f'ai/keyframes/{attachment.pk}/{sha12}-s{index}.jpg',
            )
            self.assertEqual(doc['source_file_name'], 'seal-swap.mp4')
            self.assertEqual(doc['recorded_at'], _RECORDED_AT.isoformat())
            self.assertTrue(doc['uploaded_at'])
            self.assertTrue(doc['indexed_at'])
            self.assertEqual(len(doc['media_vector']), 3072)
            self.assertEqual(doc['embedding_model'], 'fake-gemini')
            self.assertEqual(doc['embedding_dimensions'], 3072)
        self.assertEqual(docs[0]['duration_s'], 60.0)
        self.assertEqual(docs[2]['duration_s'], 20.0)

        attachment.refresh_from_db()
        stamp = attachment.metadata['ai_ingest']
        self.assertEqual(stamp['state'], 'indexed')
        self.assertEqual(stamp['sha'], sha)

    def test_storage_returned_keyframe_name_is_projected(self):
        """A storage dedupe suffix cannot leave INDEXED thumbnails dangling."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'storage-name.mp4', _MP4
        )
        real_save = default_storage.save

        def save_under_returned_name(name, content, max_length=None):
            stored_name = name.replace('.jpg', '-stored.jpg')
            return real_save(stored_name, content, max_length=max_length)

        with (
            _image_providers(),
            _video_tool_fakes(),
            mock.patch.object(
                default_storage, 'save', side_effect=save_under_returned_name
            ),
        ):
            row, _embedder, projection = self._run_media(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)
        segments = list(row.segments.order_by('segment_index'))
        self.assertEqual(len(segments), 3)
        for segment, document in zip(segments, projection.documents, strict=True):
            self.assertTrue(segment.thumbnail_path.endswith('-stored.jpg'))
            self.assertEqual(document['thumbnail_path'], segment.thumbnail_path)
            self.assertTrue(default_storage.exists(segment.thumbnail_path))

    def test_audio_only_mp4_records_terminal_unsupported_skip(self):
        """No video stream: an owner-authorized terminal SKIPPED row."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'voice-note.mp4', _MP4
        )
        with _video_tool_fakes(has_video_stream=False):
            row, embedder, projection = self._run_media(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.SKIPPED)
        self.assertEqual(row.error_code, 'ATTACHMENT_SKIP_UNSUPPORTED_TYPE')
        self.assertEqual(row.pipeline, 'video')
        self.assertEqual(embedder.video_calls, [])
        self.assertEqual(projection.operations, [])
        attachment.refresh_from_db()
        stamp = attachment.metadata['ai_ingest']
        self.assertEqual(stamp['state'], 'skipped')
        self.assertEqual(stamp['reason'], 'ATTACHMENT_SKIP_UNSUPPORTED_TYPE')

    def test_overlong_video_records_video_oversize_skip(self):
        """Beyond the duration cap the run skips before any provider work."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'marathon.mp4', _MP4
        )
        with _video_tool_fakes(duration_s=2000.0) as calls:
            row, embedder, projection = self._run_media(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.SKIPPED)
        self.assertEqual(row.error_code, 'ATTACHMENT_SKIP_VIDEO_OVERSIZE')
        self.assertEqual(row.pipeline, 'video')
        self.assertEqual(calls['cut'], [])
        self.assertEqual(embedder.video_calls, [])
        self.assertEqual(projection.operations, [])
        attachment.refresh_from_db()
        stamp = attachment.metadata['ai_ingest']
        self.assertEqual(stamp['state'], 'skipped')
        self.assertEqual(stamp['reason'], 'ATTACHMENT_SKIP_VIDEO_OVERSIZE')

    def test_oversize_segment_is_terminal_skip_without_provider_retry(self):
        """A deterministic inline-byte overflow skips the whole video once."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'high-bitrate.mp4', _MP4
        )
        overflow = AttachmentIngestionError(
            'Video segment exceeds inline limit', code='ATTACHMENT_SKIP_VIDEO_OVERSIZE'
        )
        with (
            _image_providers(),
            _video_tool_fakes() as calls,
            mock.patch(
                'aichat.services.attachment_ingestion._read_video_segment_bytes',
                side_effect=overflow,
            ),
        ):
            row, embedder, projection = self._run_media(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.SKIPPED)
        self.assertEqual(row.error_code, 'ATTACHMENT_SKIP_VIDEO_OVERSIZE')
        self.assertEqual(row.attempts, 1)
        self.assertEqual(calls['cut'], [(0.0, 60.0)])
        self.assertEqual(calls['keyframe'], [])
        self.assertEqual(embedder.video_calls, [])
        self.assertEqual(projection.operations, [])
        attachment.refresh_from_db()
        self.assertEqual(
            attachment.metadata['ai_ingest']['reason'], 'ATTACHMENT_SKIP_VIDEO_OVERSIZE'
        )

    def test_segment_failure_fails_run_then_retry_reindexes_same_ids(self):
        """A mid-loop embed failure upserts NOTHING; the retry is idempotent."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'flaky.mp4', _MP4
        )
        sha = hashlib.sha256(_MP4).hexdigest()

        class FlakyGemini(FakeGeminiClient):
            def embed_video_segment(self, data, *, mime_type):
                """Blow up on the third segment's embedding call."""
                if len(self.video_calls) == 2:
                    raise RuntimeError('provider blew up mid-video')
                return super().embed_video_segment(data, mime_type=mime_type)

        failing_projection = FakeMediaProjection()
        with (
            _image_providers(),
            _video_tool_fakes(),
            self.assertRaises(AttachmentIngestionError),
        ):
            self._run_media(
                attachment.pk,
                media_embedding_client=FlakyGemini(),
                media_projection=failing_projection,
            )
        row = AttachmentIngest.objects.get(
            attachment_id=attachment.pk, source_sha256=sha
        )
        self.assertEqual(row.state, AttachmentIngestState.FAILED)
        self.assertEqual(row.attempts, 1)
        self.assertEqual(failing_projection.operations, [])
        self.assertEqual(MediaSegment.objects.filter(ingest=row).count(), 0)
        for index in range(2):
            self.assertFalse(
                default_storage.exists(
                    f'ai/keyframes/{attachment.pk}/{sha[:12]}-s{index}.jpg'
                )
            )

        with _image_providers(), _video_tool_fakes():
            retried, _embedder, projection = self._run_media(attachment.pk)
        self.assertEqual(retried.pk, row.pk)
        self.assertEqual(retried.state, AttachmentIngestState.INDEXED)
        self.assertEqual(retried.attempts, 2)
        self.assertEqual(projection.operations, [('upsert', 3)])
        self.assertEqual(
            [doc['id'] for doc in projection.documents],
            [f'att-{attachment.pk}-{sha[:12]}-s{index}' for index in range(3)],
        )

    def test_failed_force_refresh_preserves_serving_keyframes(self):
        """An indexed revision keeps its thumbnails if a forced refresh fails."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'force-refresh.mp4', _MP4
        )
        sha12 = hashlib.sha256(_MP4).hexdigest()[:12]
        with _image_providers(), _video_tool_fakes():
            indexed, _embedder, _projection = self._run_media(attachment.pk)
        self.assertEqual(indexed.state, AttachmentIngestState.INDEXED)
        keyframes = [
            f'ai/keyframes/{attachment.pk}/{sha12}-s{index}.jpg' for index in range(3)
        ]
        for rel in keyframes:
            self.assertTrue(default_storage.exists(rel))

        class FailingGemini(FakeGeminiClient):
            def embed_video_segment(self, data, *, mime_type):
                raise RuntimeError('forced refresh failed')

        with (
            _image_providers(),
            _video_tool_fakes(),
            self.assertRaises(AttachmentIngestionError),
        ):
            self._run_media(
                attachment.pk, force=True, media_embedding_client=FailingGemini()
            )
        indexed.refresh_from_db()
        self.assertEqual(indexed.state, AttachmentIngestState.INDEXED)
        self.assertEqual(indexed.error_code, '')
        self.assertEqual(indexed.segment_count, 3)
        self.assertEqual(MediaSegment.objects.filter(ingest=indexed).count(), 3)
        for rel in keyframes:
            self.assertTrue(default_storage.exists(rel))

    def test_purge_removes_keyframe_files_segments_and_media_docs(self):
        """Deleting the attachment removes the derived keyframe files too."""
        attachment = _make_attachment('workorder', self.work_order.pk, 'gone.mp4', _MP4)
        sha12 = hashlib.sha256(_MP4).hexdigest()[:12]
        with _image_providers(), _video_tool_fakes():
            row, _embedder, _projection = self._run_media(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)
        keyframes = [
            f'ai/keyframes/{attachment.pk}/{sha12}-s{index}.jpg' for index in range(3)
        ]
        for rel in keyframes:
            self.assertTrue(default_storage.exists(rel))
        media_projection = FakeMediaProjection()
        with mock.patch(
            'ai.core.integrations.attachment_search.AttachmentSearchProjection.'
            'from_settings',
            side_effect=AssertionError('no docs client for a media-only purge'),
        ):
            purged = purge_attachment_artifacts(
                attachment.pk, media_projection=media_projection
            )
        self.assertEqual(purged, 1)
        self.assertIn(('purge', attachment.pk), media_projection.operations)
        for rel in keyframes:
            self.assertFalse(default_storage.exists(rel))
        row.refresh_from_db()
        self.assertEqual(row.state, AttachmentIngestState.DELETED)
        self.assertEqual(MediaSegment.objects.filter(ingest=row).count(), 0)

    def test_new_sha_prunes_only_the_old_shas_keyframes(self):
        """The winner's peer purge is sha12-scoped: other prefixes survive."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'rev-a.mp4', _MP4
        )
        old_sha12 = hashlib.sha256(_MP4).hexdigest()[:12]
        new_sha12 = hashlib.sha256(_MP4_ALT).hexdigest()[:12]
        with _image_providers(), _video_tool_fakes():
            first, _e, _p = self._run_media(attachment.pk)
        self.assertEqual(first.state, AttachmentIngestState.INDEXED)
        # A foreign-prefix file in the same directory must NOT be pruned.
        foreign = f'ai/keyframes/{attachment.pk}/ffffffffffff-s0.jpg'
        default_storage.save(foreign, ContentFile(b'unrelated revision keyframe'))
        with mock.patch('InvenTree.tasks.offload_task', return_value=True):
            attachment.attachment.save(
                'rev-b.mp4', SimpleUploadedFile('rev-b.mp4', _MP4_ALT)
            )
        with _image_providers(), _video_tool_fakes():
            second, _e2, projection2 = self._run_media(attachment.pk)
        self.assertEqual(second.state, AttachmentIngestState.INDEXED)
        first.refresh_from_db()
        self.assertEqual(first.state, AttachmentIngestState.SUPERSEDED)
        self.assertEqual(
            projection2.operations,
            [
                ('upsert', 3),
                ('mark_stale', attachment.pk, first.source_sha256),
                ('purge_sha', attachment.pk, first.source_sha256),
            ],
        )
        for index in range(3):
            self.assertFalse(
                default_storage.exists(
                    f'ai/keyframes/{attachment.pk}/{old_sha12}-s{index}.jpg'
                )
            )
            self.assertTrue(
                default_storage.exists(
                    f'ai/keyframes/{attachment.pk}/{new_sha12}-s{index}.jpg'
                )
            )
        self.assertTrue(default_storage.exists(foreign))

    def test_conflicting_segment_row_walks_away_without_upserting(self):
        """A real (ingest, segment_index) conflict aborts atomically: no docs."""
        attachment = _make_attachment('workorder', self.work_order.pk, 'twin.mp4', _MP4)
        real_id = media_search_document_id
        state = {'injected': False}

        def inject_conflict(attachment_id, source_sha256, *, segment_index=None):
            """Insert a conflicting row between the delete and the bulk insert."""
            if not state['injected']:
                state['injected'] = True
                twin_row = AttachmentIngest.objects.get(
                    attachment_id=attachment_id, source_sha256=source_sha256
                )
                MediaSegment.objects.create(
                    ingest=twin_row, media_type='video_segment', segment_index=0
                )
            return real_id(attachment_id, source_sha256, segment_index=segment_index)

        with (
            _image_providers(),
            _video_tool_fakes(),
            mock.patch(
                'aichat.services.attachment_ingestion.media_search_document_id',
                side_effect=inject_conflict,
            ),
        ):
            row, _embedder, projection = self._run_media(attachment.pk)
        self.assertTrue(state['injected'])
        row.refresh_from_db()
        # Walk-away: the run neither indexed nor upserted anything, and the
        # stamp never claims success.
        self.assertEqual(row.state, AttachmentIngestState.EMBEDDING)
        self.assertEqual(projection.operations, [])
        attachment.refresh_from_db()
        self.assertNotEqual(
            (attachment.metadata or {}).get('ai_ingest', {}).get('state'), 'indexed'
        )


class CrossPipelineSupersedeTests(MediaFixtureTestCase):
    """Photo<->doc replacement: each peer purges from ITS pipeline's index."""

    def test_doc_winner_purges_image_peer_from_media_projection(self):
        """A photo replaced by a document purges the photo from the media index."""
        attachment = _make_attachment(
            'assetmachine', self.machine.pk, 'was-photo.png', _PNG
        )
        with _image_providers():
            image_row, _e, _p = self._run_media(attachment.pk)
        self.assertEqual(image_row.state, AttachmentIngestState.INDEXED)
        with mock.patch('InvenTree.tasks.offload_task', return_value=True):
            attachment.attachment.save(
                'now-doc.md', SimpleUploadedFile('now-doc.md', _MD)
            )
        media_projection = FakeMediaProjection()
        doc_row, _embedder, doc_projection = self._run(
            attachment.pk, media_projection=media_projection
        )
        self.assertEqual(doc_row.state, AttachmentIngestState.INDEXED)
        self.assertEqual(doc_row.pipeline, 'doc')
        image_row.refresh_from_db()
        self.assertEqual(image_row.state, AttachmentIngestState.SUPERSEDED)
        # The doc index only gains the new revision; the photo leaves the
        # MEDIA index — the peer purge follows the PEER row's pipeline.
        self.assertEqual([op[0] for op in doc_projection.operations], ['upsert'])
        self.assertEqual(
            media_projection.operations,
            [
                ('mark_stale', attachment.pk, image_row.source_sha256),
                ('purge_sha', attachment.pk, image_row.source_sha256),
            ],
        )

    def test_image_winner_purges_doc_peer_from_docs_projection(self):
        """A document replaced by a photo purges the doc from the docs index."""
        attachment = _make_attachment(
            'assetmachine', self.machine.pk, 'was-doc.md', _MD
        )
        doc_row, _e, _p = self._run(attachment.pk)
        self.assertEqual(doc_row.state, AttachmentIngestState.INDEXED)
        with mock.patch('InvenTree.tasks.offload_task', return_value=True):
            attachment.attachment.save(
                'now-photo.png', SimpleUploadedFile('now-photo.png', _PNG)
            )
        doc_projection = FakeProjection()
        with _image_providers():
            image_row, _embedder, media_projection = self._run_media(
                attachment.pk, projection=doc_projection
            )
        self.assertEqual(image_row.state, AttachmentIngestState.INDEXED)
        self.assertEqual(image_row.pipeline, 'image')
        doc_row.refresh_from_db()
        self.assertEqual(doc_row.state, AttachmentIngestState.SUPERSEDED)
        self.assertEqual([op[0] for op in media_projection.operations], ['upsert'])
        self.assertEqual(
            doc_projection.operations,
            [
                ('mark_stale', attachment.pk, doc_row.source_sha256),
                ('purge_sha', attachment.pk, doc_row.source_sha256),
            ],
        )


class MediaThumbnailTests(MediaFixtureTestCase):
    """The three-layer thumbnail race: empty-tolerant serve, then the heal."""

    def test_absent_thumbnail_serves_empty_then_heals(self):
        """A pre-thumbnail ingest serves '' and the sweep heals it later."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'early.png', _PNG
        )
        with _image_providers():
            row, _embedder, projection = self._run_media(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)
        self.assertEqual(projection.documents[0]['thumbnail_path'], '')
        segment = MediaSegment.objects.get(ingest=row)
        self.assertEqual(segment.thumbnail_path, '')
        # rebuild_attachment lands the thumbnail after indexing.
        Attachment.objects.filter(pk=attachment.pk).update(
            thumbnail='attachments/thumbs/early.thumb.png'
        )
        heal_projection = FakeMediaProjection()
        healed = heal_media_thumbnails(media_projection=heal_projection)
        self.assertEqual(healed, 1)
        self.assertEqual(
            heal_projection.operations,
            [
                (
                    'merge_thumbnail',
                    segment.search_doc_id,
                    'attachments/thumbs/early.thumb.png',
                )
            ],
        )
        segment.refresh_from_db()
        self.assertEqual(segment.thumbnail_path, 'attachments/thumbs/early.thumb.png')

    def test_heal_without_thumbnail_builds_no_client(self):
        """While the thumbnail is still absent the heal constructs nothing."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'still-early.png', _PNG
        )
        with _image_providers():
            row, _embedder, _projection = self._run_media(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)
        with mock.patch(
            'ai.core.integrations.attachment_search.MediaSearchProjection.'
            'from_settings',
            side_effect=AssertionError('no client for a no-op heal'),
        ):
            self.assertEqual(heal_media_thumbnails(), 0)
        segment = MediaSegment.objects.get(ingest=row)
        self.assertEqual(segment.thumbnail_path, '')


class MediaOwnerDerivationTests(MediaFixtureTestCase):
    """Decision #16 scope derivation and the media owner coordinates."""

    def test_workorder_codes_derive_from_machine_client(self):
        """A machine-attributed WO stamps the machine's client code."""
        self.assertEqual(derive_client_codes('workorder', self.work_order.pk), ['acme'])

    def test_customer_attributed_workorder_fails_closed(self):
        """A customer-attributed WO stamps [] — never the machine's code."""
        self.assertEqual(
            derive_client_codes('workorder', self.work_order_customer.pk), []
        )

    def test_clientless_machine_workorder_fails_closed(self):
        """A WO on a clientless machine stamps the empty (unreachable) set."""
        self.assertEqual(
            derive_client_codes('workorder', self.work_order_orphan.pk), []
        )

    def test_broken_chains_fail_closed(self):
        """Machineless WOs and missing rows all derive [] (never widen)."""
        self.assertEqual(derive_client_codes('workorder', self.work_order_bare.pk), [])
        self.assertEqual(derive_client_codes('workorder', 999999), [])
        self.assertEqual(derive_client_codes('workorderstepexecution', 999999), [])

    def test_step_codes_follow_the_owning_work_order(self):
        """Step evidence inherits the WO scope through the application chain."""
        self.assertEqual(
            derive_client_codes('workorderstepexecution', self.step.pk), ['acme']
        )
        self.assertEqual(
            derive_client_codes('workorderstepexecution', self.step_customer.pk), []
        )

    def test_media_owner_coordinates_for_all_owners(self):
        """Each owner stamps exactly its own id pair plus machine identity."""
        self.assertEqual(
            _media_owner_coordinates('workorder', self.work_order.pk),
            {
                'work_order_id': self.work_order.pk,
                'step_execution_id': None,
                'asset_id': 'SN-100',
                'machine_name': 'Press 1',
            },
        )
        self.assertEqual(
            _media_owner_coordinates('workorderstepexecution', self.step.pk),
            {
                'work_order_id': self.work_order.pk,
                'step_execution_id': self.step.pk,
                'asset_id': 'SN-100',
                'machine_name': 'Press 1',
            },
        )
        self.assertEqual(
            _media_owner_coordinates('assetmachine', self.machine.pk),
            {
                'work_order_id': None,
                'step_execution_id': None,
                'asset_id': 'SN-100',
                'machine_name': 'Press 1',
            },
        )

    def test_media_owner_coordinates_tolerate_broken_chains(self):
        """A machineless WO and a missing owner still return the shape."""
        self.assertEqual(
            _media_owner_coordinates('workorder', self.work_order_bare.pk),
            {
                'work_order_id': self.work_order_bare.pk,
                'step_execution_id': None,
                'asset_id': '',
                'machine_name': '',
            },
        )
        self.assertEqual(
            _media_owner_coordinates('assetmachine', 999999),
            {
                'work_order_id': None,
                'step_execution_id': None,
                'asset_id': '',
                'machine_name': '',
            },
        )


class MediaPurgeTests(MediaFixtureTestCase):
    """Deletion: segments removed, and each index purged only when populated."""

    def test_purge_deletes_segments_and_hits_media_index_only(self):
        """A media-only attachment purges the media index, never the docs one."""
        attachment = _make_attachment('workorder', self.work_order.pk, 'gone.png', _PNG)
        with _image_providers():
            row, _embedder, _projection = self._run_media(attachment.pk)
        self.assertEqual(MediaSegment.objects.filter(ingest=row).count(), 1)
        media_projection = FakeMediaProjection()
        with mock.patch(
            'ai.core.integrations.attachment_search.AttachmentSearchProjection.'
            'from_settings',
            side_effect=AssertionError('no docs client for a media-only purge'),
        ):
            purged = purge_attachment_artifacts(
                attachment.pk, media_projection=media_projection
            )
        self.assertEqual(purged, 1)
        self.assertIn(('purge', attachment.pk), media_projection.operations)
        row.refresh_from_db()
        self.assertEqual(row.state, AttachmentIngestState.DELETED)
        self.assertEqual(MediaSegment.objects.filter(ingest=row).count(), 0)

    def test_doc_only_purge_never_builds_a_media_client(self):
        """A docs-only attachment purge must not construct a media projection."""
        attachment = _make_attachment(
            'assetmachine', self.machine.pk, 'doc-only.md', _MD
        )
        row, _e, _p = self._run(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)
        projection = FakeProjection()
        with mock.patch(
            'ai.core.integrations.attachment_search.MediaSearchProjection.'
            'from_settings',
            side_effect=AssertionError('no media client for a docs-only purge'),
        ):
            purged = purge_attachment_artifacts(attachment.pk, projection=projection)
        self.assertEqual(purged, 1)
        self.assertIn(('purge', attachment.pk), projection.operations)
        self.assertEqual(
            AttachmentChunk.objects.filter(ingest__attachment_id=attachment.pk).count(),
            0,
        )


class WorkOrderRestampTests(MediaFixtureTestCase):
    """§6.5 for evidence media: WO restamp service and its receiver gating."""

    def test_restamp_updates_wo_and_step_rows_after_machine_change(self):
        """A machine change re-stamps WO-owned and step-owned media rows."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'restamp.png', _PNG
        )
        with _image_providers():
            row, _embedder, _projection = self._run_media(attachment.pk)
        self.assertEqual(row.client_codes, ['acme'])
        step_row = AttachmentIngest.objects.create(
            attachment_id=990001,
            model_type='workorderstepexecution',
            model_id=self.step.pk,
            source_sha256='e' * 64,
            pipeline='image',
            state=AttachmentIngestState.INDEXED,
            client_codes=['acme'],
        )
        self.work_order.machine = self.machine_zeta
        with mock.patch('InvenTree.tasks.offload_task', return_value=True):
            self.work_order.save()
        projection = FakeMediaProjection()
        touched = restamp_work_order_media_client_codes(
            self.work_order.pk, media_projection=projection
        )
        self.assertEqual(touched, 2)
        metadata_ops = {
            op[1]: dict(op[2])
            for op in projection.operations
            if op[0] == 'merge_metadata'
        }
        # Scope AND coordinates travel together: the WO now belongs to the
        # zeta machine, so its photos re-stamp asset_id/machine_name too.
        self.assertEqual(metadata_ops[attachment.pk]['client_codes'], ('zeta',))
        self.assertEqual(
            metadata_ops[attachment.pk]['asset_id'], self.machine_zeta.serial
        )
        self.assertEqual(
            metadata_ops[attachment.pk]['machine_name'], self.machine_zeta.name
        )
        self.assertEqual(metadata_ops[990001]['client_codes'], ('zeta',))
        row.refresh_from_db()
        step_row.refresh_from_db()
        self.assertEqual(row.client_codes, ['zeta'])
        self.assertEqual(step_row.client_codes, ['zeta'])

    def test_machine_restamp_routes_media_rows_to_the_media_index(self):
        """Machine-owned photos re-stamp via the MEDIA projection.

        Review finding (R3, critical): without the doc loop's pipeline
        filter, machine-owned IMAGE rows were consumed by the docs-index
        merge (a no-op), the registry was updated anyway, and the media
        loop's change check then skipped them — the media index kept the
        OLD tenant's codes forever, self-masking on every later restamp.
        """
        attachment = _make_attachment(
            'assetmachine', self.machine.pk, 'machine-photo.png', _PNG
        )
        with _image_providers():
            row, _embedder, _projection = self._run_media(attachment.pk)
        self.assertEqual(row.client_codes, ['acme'])
        self.machine.client = self.client_zeta
        with mock.patch('InvenTree.tasks.offload_task', return_value=True):
            self.machine.save()

        doc_projection = FakeProjection()
        with mock.patch(
            'ai.core.integrations.attachment_search.MediaSearchProjection.'
            'from_settings',
            return_value=FakeMediaProjection(),
        ) as media_factory:
            touched = restamp_machine_client_codes(
                self.machine.pk, projection=doc_projection
            )
        media_projection = media_factory.return_value
        self.assertGreaterEqual(touched, 1)
        # The docs projection never saw the media row...
        self.assertNotIn(('merge', attachment.pk, ('zeta',)), doc_projection.operations)
        # ...and the media projection re-stamped scope AND coordinates.
        metadata_ops = [
            op for op in media_projection.operations if op[0] == 'merge_metadata'
        ]
        self.assertTrue(
            any(op[1] == attachment.pk for op in metadata_ops), metadata_ops
        )
        row.refresh_from_db()
        self.assertEqual(row.client_codes, ['zeta'])

    def test_restamp_noop_is_index_diffed(self):
        """Unchanged rows cost one index read and zero writes.

        The projection diffs against the serving documents (coordinate
        drift is invisible to the registry), so the F-19 guard moved to the
        receiver's existence gate; a no-op restamp merges nothing and never
        touches the registry row.
        """
        attachment = _make_attachment('workorder', self.work_order.pk, 'noop.png', _PNG)
        with _image_providers():
            row, _embedder, _projection = self._run_media(attachment.pk)
        self.assertEqual(row.client_codes, ['acme'])

        class _NoChangeProjection(FakeMediaProjection):
            def merge_media_metadata(self, *, attachment_id, fields):
                self.operations.append(('merge_metadata', attachment_id, None))
                return 0

        projection = _NoChangeProjection()
        self.assertEqual(
            restamp_work_order_media_client_codes(
                self.work_order.pk, media_projection=projection
            ),
            0,
        )
        row.refresh_from_db()
        self.assertEqual(row.client_codes, ['acme'])

    @override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True)
    def test_work_order_saved_offloads_only_when_media_rows_exist(self):
        """The WO receiver is existence-gated: no rows, no offload."""
        from aichat import tasks as aichat_tasks

        def _restamp_offloads(mocked):
            return [
                call
                for call in mocked.call_args_list
                if call.args and call.args[0] is aichat_tasks.restamp_work_order_media
            ]

        with mock.patch('InvenTree.tasks.offload_task', return_value=True) as off:
            self.work_order.save()
        self.assertEqual(_restamp_offloads(off), [])
        AttachmentIngest.objects.create(
            attachment_id=990002,
            model_type='workorder',
            model_id=self.work_order.pk,
            source_sha256='f' * 64,
            pipeline='image',
            state=AttachmentIngestState.INDEXED,
        )
        with mock.patch('InvenTree.tasks.offload_task', return_value=True) as off:
            self.work_order.save()
        offloads = _restamp_offloads(off)
        self.assertEqual(len(offloads), 1)
        self.assertEqual(offloads[0].args[1], self.work_order.pk)
        self.assertTrue(offloads[0].kwargs['force_async'])
        self.assertEqual(offloads[0].kwargs['group'], 'ai-ingest')
