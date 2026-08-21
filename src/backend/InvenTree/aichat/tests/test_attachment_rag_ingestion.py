"""R1 attachment RAG: receivers, router skips, doc path, scope, purge, backfill."""

import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from ai.core.config import Settings
from aichat.models import AttachmentChunk, AttachmentIngest, AttachmentIngestState
from aichat.services.attachment_ingestion import (
    AttachmentIngestionError,
    derive_client_codes,
    purge_attachment_artifacts,
    restamp_part_client_codes,
    run_ingest,
)
from assets.models import AssetMachine, Client, MachinePart
from common.models import Attachment
from part.models import Part

_MEDIA_ROOT = tempfile.mkdtemp(prefix='aimms-rag-test-')

_MD = b'# Press Manual\n\n## Safety\n\nLock out power before service.\n'
_PDF_HEAD = b'%PDF-1.7\n1 0 obj\n<<>>\nendobj\n'
_PNG_HEAD = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32
_XLSX_HEAD = b'PK\x03\x04' + b'\x00' * 32
_WAV_HEAD = b'RIFF\x00\x00\x00\x00WAVEfmt ' + b'\x00' * 32
_MP4_HEAD = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 32


def _ai_settings(**overrides) -> Settings:
    """Valid attachment-RAG configuration; individual tests override pieces."""
    values = {
        'FEATURE_ATTACHMENT_RAG_INGEST': True,
        'COHERE_EMBED_ENDPOINT': 'https://cohere.example',
        'AZURE_SEARCH_ENDPOINT': 'https://search.example',
        'single_site_policy_key': 'site-a',
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeEmbeddingClient:
    """Deterministic vectors; counts calls for short-circuit assertions."""

    model = 'fake-cohere'
    dimensions = 1536

    def __init__(self):
        """Initialize recorders."""
        self.calls = 0

    def embed_documents(self, texts):
        """Return fixed-width vectors and count the call."""
        self.calls += 1
        return [[0.5] * self.dimensions for _ in texts]

    def embed_query(self, text):
        """Return one query vector and count the call (retrieval side, R2)."""
        self.calls += 1
        return [0.5] * self.dimensions


class FakeProjection:
    """Records projection operations in order."""

    index_name = 'aimms-attachment-docs-v1'

    def __init__(self):
        """Initialize recorders."""
        self.operations = []
        self.documents = []
        self.closed = False

    def upsert_documents(self, documents):
        """Record an upsert with its document count."""
        self.operations.append(('upsert', len(documents)))
        self.documents = list(documents)

    def prune_stale_sha(self, *, attachment_id, keep_sha256):
        """Record a prune call."""
        self.operations.append(('prune', attachment_id, keep_sha256))
        return 0

    def mark_sha_stale(self, *, attachment_id, source_sha256):
        """Record an is_current=false merge for one revision."""
        self.operations.append(('mark_stale', attachment_id, source_sha256))
        return 1

    def purge_sha(self, *, attachment_id, source_sha256):
        """Record a sha-scoped delete for one revision."""
        self.operations.append(('purge_sha', attachment_id, source_sha256))
        return 1

    def purge_attachment(self, *, attachment_id):
        """Record a purge call."""
        self.operations.append(('purge', attachment_id))
        return 1

    def merge_client_codes(self, *, attachment_id, client_codes):
        """Record a metadata-only scope merge."""
        self.operations.append(('merge', attachment_id, tuple(client_codes)))
        return 1

    def close(self):
        """Record client release."""
        self.closed = True


def _make_attachment(model_type, model_id, name, content, **extra):
    """Create an attachment with background offloads suppressed."""
    with mock.patch('InvenTree.tasks.offload_task', return_value=True):
        return Attachment.objects.create(
            model_type=model_type,
            model_id=model_id,
            attachment=SimpleUploadedFile(name, content),
            comment='test',
            **extra,
        )


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class RagFixtureTestCase(TestCase):
    """Shared owners for the RAG tests."""

    @classmethod
    def setUpTestData(cls):
        """One client-scoped machine plus one linked and one unlinked part."""
        cls.client_acme = Client.objects.create(name='Acme', code='acme')
        cls.client_zeta = Client.objects.create(name='Zeta', code='zeta')
        cls.machine = AssetMachine.objects.create(
            name='Press 1', client=cls.client_acme, serial='SN-100'
        )
        cls.machine_zeta = AssetMachine.objects.create(
            name='Press 2', client=cls.client_zeta, serial='SN-200'
        )
        cls.machine_unscoped = AssetMachine.objects.create(name='Orphan Press')
        cls.part = Part.objects.create(name='Seal Kit', description='seals')
        cls.part_unlinked = Part.objects.create(name='Loose Part', description='x')
        MachinePart.objects.create(machine=cls.machine, part=cls.part)

    def _run(self, attachment_id, *, settings_overrides=None, **kwargs):
        """run_ingest under a valid AI config, Django flag on, and fakes."""
        embedder = kwargs.pop('embedding_client', FakeEmbeddingClient())
        projection = kwargs.pop('projection', FakeProjection())
        ai_settings = _ai_settings(**(settings_overrides or {}))
        with (
            override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True),
            mock.patch('ai.core.config.get_settings', return_value=ai_settings),
        ):
            row = run_ingest(
                attachment_id,
                embedding_client=embedder,
                projection=projection,
                **kwargs,
            )
        return row, embedder, projection


class ReceiverTests(RagFixtureTestCase):
    """Signal-side gating: flag, allow-list, structural skips, stamp, purge."""

    def _ingest_offloads(self, mocked):
        """Filter offload calls down to ingest_attachment."""
        from aichat import tasks as aichat_tasks

        return [
            call
            for call in mocked.call_args_list
            if call.args and call.args[0] is aichat_tasks.ingest_attachment
        ]

    def test_flag_off_never_offloads(self):
        """Dark flag means the receiver never offloads."""
        with mock.patch('InvenTree.tasks.offload_task', return_value=True) as off:
            Attachment.objects.create(
                model_type='part',
                model_id=self.part.pk,
                attachment=SimpleUploadedFile('m.md', _MD),
                comment='t',
            )
        self.assertEqual(self._ingest_offloads(off), [])

    @override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True)
    def test_flag_on_offloads_ingest(self):
        """Allow-listed upload offloads with group/async set."""
        ai_settings = _ai_settings()
        with (
            mock.patch('InvenTree.tasks.offload_task', return_value=True) as off,
            mock.patch('ai.core.config.get_settings', return_value=ai_settings),
        ):
            attachment = Attachment.objects.create(
                model_type='part',
                model_id=self.part.pk,
                attachment=SimpleUploadedFile('m.md', _MD),
                comment='t',
            )
        offloads = self._ingest_offloads(off)
        self.assertGreaterEqual(len(offloads), 1)
        for call in offloads:
            self.assertEqual(call.args[1], attachment.pk)
            self.assertTrue(call.kwargs['force_async'])
            self.assertEqual(call.kwargs['group'], 'ai-ingest')

    @override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True)
    def test_structural_and_allowlist_skips(self):
        """Link-only, SVG, and foreign owners never offload."""
        ai_settings = _ai_settings()
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        with (
            mock.patch('InvenTree.tasks.offload_task', return_value=True) as off,
            mock.patch('ai.core.config.get_settings', return_value=ai_settings),
        ):
            Attachment.objects.create(
                model_type='part',
                model_id=self.part.pk,
                link='https://example.com/doc',
                comment='link only',
            )
            Attachment.objects.create(
                model_type='part',
                model_id=self.part.pk,
                attachment=SimpleUploadedFile('img.svg', svg),
                comment='svg',
            )
            Attachment.objects.create(
                model_type='build',
                model_id=999,
                attachment=SimpleUploadedFile('m.md', _MD),
                comment='owner',
            )
        self.assertEqual(self._ingest_offloads(off), [])

    @override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True)
    def test_matching_stamp_short_circuits(self):
        """A matching v2 indexed stamp suppresses re-offload; failed does not."""
        from aichat.services.attachment_ingestion import _build_stamp, storage_mtime

        attachment = _make_attachment('part', self.part.pk, 'm.md', _MD)
        attachment.refresh_from_db()
        stamp = _build_stamp(
            attachment, 'x' * 64, 'indexed', mtime=storage_mtime(attachment)
        )
        Attachment.objects.filter(pk=attachment.pk).update(
            metadata={'ai_ingest': stamp}
        )
        attachment.refresh_from_db()
        ai_settings = _ai_settings()
        with (
            mock.patch('InvenTree.tasks.offload_task', return_value=True) as off,
            mock.patch('ai.core.config.get_settings', return_value=ai_settings),
        ):
            attachment.save()
        self.assertEqual(self._ingest_offloads(off), [])
        # A failed stamp must NOT short-circuit: retries stay reachable.
        stamp['state'] = 'failed'
        Attachment.objects.filter(pk=attachment.pk).update(
            metadata={'ai_ingest': stamp}
        )
        attachment.refresh_from_db()
        with (
            mock.patch('InvenTree.tasks.offload_task', return_value=True) as off,
            mock.patch('ai.core.config.get_settings', return_value=ai_settings),
        ):
            attachment.save()
        self.assertEqual(len(self._ingest_offloads(off)), 1)

    @override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True)
    def test_v1_stamp_never_matches(self):
        """Pre-v2 stamps (no version) revive on the next save (F-03)."""
        attachment = _make_attachment('part', self.part.pk, 'v1.md', _MD)
        attachment.refresh_from_db()
        stamp = {
            'sha': 'x' * 64,
            'name': attachment.attachment.name,
            'size': attachment.file_size,
            'state': 'indexed',
        }
        Attachment.objects.filter(pk=attachment.pk).update(
            metadata={'ai_ingest': stamp}
        )
        attachment.refresh_from_db()
        with (
            mock.patch('InvenTree.tasks.offload_task', return_value=True) as off,
            mock.patch('ai.core.config.get_settings', return_value=_ai_settings()),
        ):
            attachment.save()
        self.assertEqual(len(self._ingest_offloads(off)), 1)

    @override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True)
    def test_replaced_content_same_name_size_reoffloads(self):
        """In-place replacement at identical name+size revives via mtime (F-02)."""
        import os
        import time

        from aichat.services.attachment_ingestion import _build_stamp, storage_mtime

        attachment = _make_attachment('part', self.part.pk, 'swap.md', _MD)
        attachment.refresh_from_db()
        stamp = _build_stamp(
            attachment, 'x' * 64, 'indexed', mtime=storage_mtime(attachment)
        )
        Attachment.objects.filter(pk=attachment.pk).update(
            metadata={'ai_ingest': stamp}
        )
        attachment.refresh_from_db()
        # Same-length different bytes, written in place after an mtime tick.
        replacement = _MD[:-2] + b'X\n'
        self.assertEqual(len(replacement), len(_MD))
        time.sleep(0.02)
        with attachment.attachment.open('wb') as handle:
            handle.write(replacement)
        os.utime(attachment.attachment.path)
        with (
            mock.patch('InvenTree.tasks.offload_task', return_value=True) as off,
            mock.patch('ai.core.config.get_settings', return_value=_ai_settings()),
        ):
            attachment.save()
        self.assertEqual(len(self._ingest_offloads(off)), 1)

    @override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True)
    def test_flag_dependent_skip_stamp_revives_on_flip(self):
        """PIPELINE_DISABLED stamps stop matching once the flag is on (F-10)."""
        from aichat.services.attachment_ingestion import _build_stamp, storage_mtime

        attachment = _make_attachment('part', self.part.pk, 'revive.md', _MD)
        attachment.refresh_from_db()
        stamp = _build_stamp(
            attachment,
            'x' * 64,
            'skipped',
            reason='ATTACHMENT_SKIP_PIPELINE_DISABLED',
            mtime=storage_mtime(attachment),
        )
        Attachment.objects.filter(pk=attachment.pk).update(
            metadata={'ai_ingest': stamp}
        )
        attachment.refresh_from_db()
        # Flag still off: stamp suppresses.
        dark = _ai_settings(FEATURE_ATTACHMENT_RAG_INGEST=False)
        with (
            mock.patch('InvenTree.tasks.offload_task', return_value=True) as off,
            mock.patch('ai.core.config.get_settings', return_value=dark),
        ):
            attachment.save()
        self.assertEqual(self._ingest_offloads(off), [])
        # Flag on: the same stamp revives.
        with (
            mock.patch('InvenTree.tasks.offload_task', return_value=True) as off,
            mock.patch('ai.core.config.get_settings', return_value=_ai_settings()),
        ):
            attachment.save()
        self.assertEqual(len(self._ingest_offloads(off)), 1)

    @override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True)
    def test_media_dark_stamp_revives_only_on_full_flag_conjunction(self):
        """MEDIA_PIPELINE_DARK stamps revive exactly when BOTH media flags are on.

        The receiver revival clause reads ``media_ingest_enabled`` — the SAME
        predicate the R3 router enforces — so a partial flip (either plane
        alone) keeps suppressing, and the full conjunction re-offloads once.
        """
        from aichat.services.attachment_ingestion import _build_stamp, storage_mtime

        attachment = _make_attachment(
            'workorder', 4242, 'evidence.png', _PNG_HEAD
        )
        attachment.refresh_from_db()
        stamp = _build_stamp(
            attachment,
            'x' * 64,
            'skipped',
            reason='ATTACHMENT_SKIP_MEDIA_PIPELINE_DARK',
            mtime=storage_mtime(attachment),
        )
        Attachment.objects.filter(pk=attachment.pk).update(
            metadata={'ai_ingest': stamp}
        )
        attachment.refresh_from_db()
        media_on = _ai_settings(
            FEATURE_MEDIA_RAG_INGEST=True,
            GCP_PROJECT_ID='p',
            GCP_LOCATION='us-central1',
            GCP_CREDENTIALS_PATH='/tmp/wif.json',
            AZURE_OPENAI_ENDPOINT='https://openai.example',
        )
        # AI-plane flag on, Django co-gate off: still suppressed.
        with (
            mock.patch('InvenTree.tasks.offload_task', return_value=True) as off,
            mock.patch('ai.core.config.get_settings', return_value=media_on),
        ):
            attachment.save()
        self.assertEqual(self._ingest_offloads(off), [])
        # Django co-gate on, AI-plane flag off: still suppressed.
        with (
            override_settings(AIMMS_MEDIA_RAG_ENABLED=True),
            mock.patch('InvenTree.tasks.offload_task', return_value=True) as off,
            mock.patch('ai.core.config.get_settings', return_value=_ai_settings()),
        ):
            attachment.save()
        self.assertEqual(self._ingest_offloads(off), [])
        # Both planes on: the stamp stops matching and revives exactly once.
        with (
            override_settings(AIMMS_MEDIA_RAG_ENABLED=True),
            mock.patch('InvenTree.tasks.offload_task', return_value=True) as off,
            mock.patch('ai.core.config.get_settings', return_value=media_on),
        ):
            attachment.save()
        self.assertEqual(len(self._ingest_offloads(off)), 1)

    @override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True)
    def test_broken_ai_config_fails_loudly_not_silently(self):
        """A broken AI config must offload (loud task failure), not swallow."""
        from aichat.services.attachment_ingestion import _build_stamp, storage_mtime

        attachment = _make_attachment('part', self.part.pk, 'loud.md', _MD)
        attachment.refresh_from_db()
        stamp = _build_stamp(
            attachment,
            'x' * 64,
            'skipped',
            reason='ATTACHMENT_SKIP_PIPELINE_DISABLED',
            mtime=storage_mtime(attachment),
        )
        Attachment.objects.filter(pk=attachment.pk).update(
            metadata={'ai_ingest': stamp}
        )
        attachment.refresh_from_db()
        with (
            mock.patch('InvenTree.tasks.offload_task', return_value=True) as off,
            mock.patch(
                'ai.core.config.get_settings', side_effect=RuntimeError('bad config')
            ),
        ):
            attachment.save()
        self.assertEqual(len(self._ingest_offloads(off)), 1)

    def test_delete_offloads_purge_only_when_rows_exist(self):
        """Purge offloads only when ingest rows exist."""
        from aichat import tasks as aichat_tasks

        attachment = _make_attachment('part', self.part.pk, 'a.md', _MD)
        with mock.patch('InvenTree.tasks.offload_task', return_value=True) as off:
            attachment.delete()
        purges = [
            call
            for call in off.call_args_list
            if call.args and call.args[0] is aichat_tasks.purge_attachment
        ]
        self.assertEqual(purges, [])

        attachment = _make_attachment('part', self.part.pk, 'b.md', _MD)
        AttachmentIngest.objects.create(
            attachment_id=attachment.pk,
            model_type='part',
            model_id=self.part.pk,
            source_sha256='c' * 64,
            pipeline='doc',
            state=AttachmentIngestState.INDEXED,
        )
        with mock.patch('InvenTree.tasks.offload_task', return_value=True) as off:
            attachment.delete()
        purges = [
            call
            for call in off.call_args_list
            if call.args and call.args[0] is aichat_tasks.purge_attachment
        ]
        self.assertEqual(len(purges), 1)


class RouterSkipTests(RagFixtureTestCase):
    """Decision #10/#11: reachable-but-not-ingested content records a skip row."""

    def _assert_skip(self, attachment, reason, pipeline):
        """Assert one recorded skip row and its metadata stamp."""
        row, _embedder, _projection = self._run(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.SKIPPED)
        self.assertEqual(row.error_code, reason)
        self.assertEqual(row.pipeline, pipeline)
        attachment.refresh_from_db()
        self.assertEqual(attachment.metadata['ai_ingest']['state'], 'skipped')
        return row

    def test_xlsx_is_recorded_skip(self):
        """XLSX routes to a recorded skip (decision #11)."""
        attachment = _make_attachment('part', self.part.pk, 'bom.xlsx', _XLSX_HEAD)
        self._assert_skip(attachment, 'ATTACHMENT_SKIP_XLSX', 'doc')

    def test_part_image_is_recorded_skip(self):
        """Part images are explicit v1 skips (decision #10)."""
        attachment = _make_attachment('part', self.part.pk, 'photo.png', _PNG_HEAD)
        self._assert_skip(attachment, 'ATTACHMENT_SKIP_PART_IMAGE', 'image')

    def test_machine_image_skips_dark_media_pipeline(self):
        """Machine photos skip while media is dark."""
        attachment = _make_attachment(
            'assetmachine', self.machine.pk, 'nameplate.png', _PNG_HEAD
        )
        self._assert_skip(attachment, 'ATTACHMENT_SKIP_MEDIA_PIPELINE_DARK', 'image')

    def test_extension_sniff_mismatch_is_recorded(self):
        """Bytes that contradict the extension are recorded skips."""
        attachment = _make_attachment(
            'part', self.part.pk, 'fake.pdf', b'plain text, not a pdf'
        )
        self._assert_skip(attachment, 'ATTACHMENT_SKIP_SNIFF_MISMATCH', 'doc')

    def test_dark_ai_plane_records_pipeline_disabled(self):
        """AI-plane flag off records a pipeline-disabled skip."""
        attachment = _make_attachment('part', self.part.pk, 'm.md', _MD)
        row, _embedder, _projection = self._run(
            attachment.pk, settings_overrides={'FEATURE_ATTACHMENT_RAG_INGEST': False}
        )
        self.assertEqual(row.state, AttachmentIngestState.SKIPPED)
        self.assertEqual(row.error_code, 'ATTACHMENT_SKIP_PIPELINE_DISABLED')

    def test_skip_never_demotes_an_indexed_row(self):
        """A later skip outcome cannot demote an indexed revision."""
        attachment = _make_attachment('part', self.part.pk, 'keep.md', _MD)
        row, _embedder, _projection = self._run(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)
        demoted, _e, _p = self._run(
            attachment.pk, settings_overrides={'FEATURE_ATTACHMENT_RAG_INGEST': False}
        )
        self.assertEqual(demoted.pk, row.pk)
        self.assertEqual(demoted.state, AttachmentIngestState.INDEXED)


class DocIngestTests(RagFixtureTestCase):
    """The doc path: extraction, chunking, projection, supersede, failure."""

    def test_markdown_end_to_end(self):
        """Markdown upload lands indexed with correct projection fields."""
        attachment = _make_attachment(
            'assetmachine', self.machine.pk, 'press-manual.md', _MD
        )
        row, embedder, projection = self._run(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)
        self.assertEqual(row.pipeline, 'doc')
        self.assertEqual(row.extractor, 'direct')
        self.assertEqual(row.embedding_model, 'fake-cohere')
        self.assertEqual(row.embedding_dimensions, 1536)
        self.assertEqual(row.search_index_name, 'aimms-attachment-docs-v1')
        self.assertEqual(row.client_codes, ['acme'])
        self.assertGreaterEqual(row.chunk_count, 1)
        self.assertEqual(
            AttachmentChunk.objects.filter(ingest=row).count(), row.chunk_count
        )
        self.assertEqual(embedder.calls, 1)
        # A fresh (peerless) document only upserts: pruning is per observed
        # superseded revision, never a blanket sweep (F-06 fix).
        self.assertEqual([op[0] for op in projection.operations], ['upsert'])
        self.assertIsNotNone(row.claimed_at)
        doc = projection.documents[0]
        self.assertEqual(doc['access_class'], 'attachment_uploaded')
        self.assertEqual(doc['scope_key'], 'site-a')
        self.assertEqual(doc['client_codes'], ['acme'])
        self.assertEqual(doc['asset_id'], 'SN-100')
        self.assertEqual(doc['machine_name'], 'Press 1')
        self.assertEqual(doc['doc_type'], 'manual')
        self.assertEqual(doc['embedding_model'], 'fake-cohere')
        self.assertEqual(doc['id'], f'att-{attachment.pk}-{row.source_sha256[:12]}-c0')
        self.assertEqual(len(doc['text_vector']), 1536)
        attachment.refresh_from_db()
        stamp = attachment.metadata['ai_ingest']
        self.assertEqual(stamp['state'], 'indexed')
        self.assertEqual(stamp['sha'], row.source_sha256)
        self.assertEqual(stamp['v'], 2)
        self.assertIn('mtime', stamp)

    def test_same_sha_short_circuits(self):
        """Identical content re-runs do not re-embed or re-project."""
        attachment = _make_attachment('part', self.part.pk, 'kit.md', _MD)
        row, _embedder, _projection = self._run(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)
        again, embedder2, projection2 = self._run(attachment.pk)
        self.assertEqual(again.pk, row.pk)
        self.assertEqual(embedder2.calls, 0)
        self.assertEqual(projection2.operations, [])

    def test_new_sha_supersedes_with_zero_gap_ordering(self):
        """New revision upserts before the old one is pruned."""
        attachment = _make_attachment('part', self.part.pk, 'rev-a.md', _MD)
        first, _embedder, _projection = self._run(attachment.pk)
        with mock.patch('InvenTree.tasks.offload_task', return_value=True):
            attachment.attachment.save(
                'rev-b.md',
                SimpleUploadedFile(
                    'rev-b.md', _MD + b'\n## Revision B\n\nNew steps.\n'
                ),
            )
        second, _embedder2, projection2 = self._run(attachment.pk)
        self.assertNotEqual(second.pk, first.pk)
        self.assertEqual(second.state, AttachmentIngestState.INDEXED)
        first.refresh_from_db()
        self.assertEqual(first.state, AttachmentIngestState.SUPERSEDED)
        # Zero-gap ordering (decision #15): upsert first, then the observed
        # old revision is stale-marked (F-09) and purged sha-scoped.
        self.assertEqual(
            projection2.operations,
            [
                ('upsert', projection2.operations[0][1]),
                ('mark_stale', attachment.pk, first.source_sha256),
                ('purge_sha', attachment.pk, first.source_sha256),
            ],
        )

    def test_pdf_without_di_fails_closed(self):
        """No DI means failure, never a silent fallback (decision #12)."""
        attachment = _make_attachment('part', self.part.pk, 'spec.pdf', _PDF_HEAD)
        with (
            mock.patch(
                'ai.core.integrations.doc_intelligence.get_doc_intelligence_client',
                return_value=None,
            ),
            self.assertRaises(AttachmentIngestionError) as caught,
        ):
            self._run(attachment.pk)
        self.assertEqual(caught.exception.code, 'ATTACHMENT_EXTRACTION_UNAVAILABLE')
        row = AttachmentIngest.objects.get(attachment_id=attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.FAILED)
        self.assertEqual(row.error_code, 'ATTACHMENT_EXTRACTION_UNAVAILABLE')
        self.assertEqual(row.attempts, 1)

    def test_pdf_with_explicit_pypdf_override(self):
        """The explicit override stamps extractor=pypdf_override."""
        attachment = _make_attachment('part', self.part.pk, 'legacy.pdf', _PDF_HEAD)
        with (
            mock.patch(
                'ai.core.integrations.doc_intelligence.get_doc_intelligence_client',
                return_value=None,
            ),
            mock.patch(
                'aichat.services.attachment_ingestion._extract_with_pypdf',
                return_value=('# Legacy\n\nExtracted text.', [0]),
            ),
        ):
            row, _embedder, _projection = self._run(attachment.pk, allow_pypdf=True)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)
        self.assertEqual(row.extractor, 'pypdf_override')

    def test_failed_attempts_cap_stops_retries(self):
        """The attempts cap halts further ingestion work."""
        attachment = _make_attachment('part', self.part.pk, 'flaky.md', _MD)
        with mock.patch('InvenTree.tasks.offload_task', return_value=True):
            attachment.refresh_from_db()
        import hashlib

        sha = hashlib.sha256(_MD).hexdigest()
        AttachmentIngest.objects.create(
            attachment_id=attachment.pk,
            model_type='part',
            model_id=self.part.pk,
            source_sha256=sha,
            pipeline='doc',
            state=AttachmentIngestState.FAILED,
            attempts=3,
        )
        row, embedder, _projection = self._run(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.FAILED)
        self.assertEqual(row.attempts, 3)
        self.assertEqual(embedder.calls, 0)

    def test_blank_text_records_empty_skip(self):
        """Whitespace-only text records an empty-content skip."""
        attachment = _make_attachment('part', self.part.pk, 'blank.txt', b'   \n  ')
        row, _embedder, _projection = self._run(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.SKIPPED)
        self.assertEqual(row.error_code, 'ATTACHMENT_SKIP_EMPTY_CONTENT')

    def test_unresolved_scope_refuses(self):
        """Empty site scope refuses before any row is created."""
        attachment = _make_attachment('part', self.part.pk, 'scoped.md', _MD)
        with self.assertRaises(AttachmentIngestionError) as caught:
            self._run(attachment.pk, settings_overrides={'single_site_policy_key': ''})
        self.assertEqual(caught.exception.code, 'ATTACHMENT_INGEST_SCOPE_UNRESOLVED')
        self.assertFalse(
            AttachmentIngest.objects.filter(attachment_id=attachment.pk).exists()
        )


class ClientCodeTests(RagFixtureTestCase):
    """Decision #5/#16 derivations plus §6.5 re-stamp and purge."""

    def test_machine_docs_carry_client_code(self):
        """Machine docs stamp the owning client code."""
        self.assertEqual(derive_client_codes('assetmachine', self.machine.pk), ['acme'])

    def test_clientless_machine_is_fail_closed(self):
        """A clientless machine derives an empty (unreachable) set."""
        self.assertEqual(
            derive_client_codes('assetmachine', self.machine_unscoped.pk), []
        )

    def test_part_codes_are_distinct_sorted_installations(self):
        """Part codes union distinct installation clients."""
        MachinePart.objects.create(machine=self.machine_zeta, part=self.part)
        self.assertEqual(derive_client_codes('part', self.part.pk), ['acme', 'zeta'])

    def test_unlinked_part_falls_back_to_internal(self):
        """Unlinked parts keep internal visibility."""
        self.assertEqual(
            derive_client_codes('part', self.part_unlinked.pk), ['internal']
        )

    def test_restamp_part_merges_new_codes(self):
        """Linkage change re-stamps codes via metadata-only merge."""
        attachment = _make_attachment('part', self.part_unlinked.pk, 'orphan.md', _MD)
        row, _embedder, _projection = self._run(attachment.pk)
        self.assertEqual(row.client_codes, ['internal'])
        MachinePart.objects.create(machine=self.machine, part=self.part_unlinked)
        projection = FakeProjection()
        touched = restamp_part_client_codes(
            self.part_unlinked.pk, projection=projection
        )
        self.assertEqual(touched, 1)
        self.assertIn(('merge', attachment.pk, ('acme',)), projection.operations)
        row.refresh_from_db()
        self.assertEqual(row.client_codes, ['acme'])

    def test_purge_deletes_chunks_and_tombstones_rows(self):
        """Purge removes chunks and tombstones registry rows."""
        attachment = _make_attachment('part', self.part.pk, 'purge-me.md', _MD)
        row, _embedder, _projection = self._run(attachment.pk)
        self.assertGreater(AttachmentChunk.objects.filter(ingest=row).count(), 0)
        projection = FakeProjection()
        purged = purge_attachment_artifacts(attachment.pk, projection=projection)
        self.assertEqual(purged, 1)
        self.assertIn(('purge', attachment.pk), projection.operations)
        row.refresh_from_db()
        self.assertEqual(row.state, AttachmentIngestState.DELETED)
        self.assertEqual(AttachmentChunk.objects.filter(ingest=row).count(), 0)


class BackfillCommandTests(RagFixtureTestCase):
    """Docs-only backfill: dry-run routing report and the flag gate."""

    def test_dry_run_reports_decisions_without_ingesting(self):
        """Dry run reports routing and writes nothing."""
        _make_attachment('part', self.part.pk, 'manual.md', _MD)
        _make_attachment('part', self.part.pk, 'bom.xlsx', _XLSX_HEAD)
        _make_attachment('part', self.part.pk, 'photo.png', _PNG_HEAD)
        from io import StringIO

        out = StringIO()
        ai_settings = _ai_settings()
        with mock.patch('ai.core.config.get_settings', return_value=ai_settings):
            call_command('ingest_existing_attachments', '--dry-run', stdout=out)
        report = out.getvalue()
        self.assertIn('INGEST', report)
        self.assertIn('ATTACHMENT_SKIP_XLSX', report)
        # R3: image extensions are walked and report their route decision
        # (part imagery stays excluded, so the decision here is the skip).
        # Storage may dedupe-suffix the stem, so match the walked line.
        self.assertRegex(report, r'photo\S*\.png\tATTACHMENT_SKIP_PART_IMAGE')
        self.assertFalse(AttachmentIngest.objects.exists())

    def test_live_run_requires_django_flag(self):
        """Live backfill refuses while the Django flag is dark."""
        with self.assertRaises(CommandError):
            call_command('ingest_existing_attachments')

    @override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True)
    def test_live_run_shares_clients_and_honors_limit(self):
        """Live backfill builds one client pair and stops at --limit."""
        from io import StringIO

        _make_attachment('part', self.part.pk, 'one.md', _MD)
        _make_attachment('part', self.part.pk, 'two.md', _MD + b'\nMore.\n')
        embedder = FakeEmbeddingClient()
        projection = FakeProjection()
        out = StringIO()
        with (
            mock.patch('ai.core.config.get_settings', return_value=_ai_settings()),
            mock.patch(
                'ai.core.integrations.embeddings_cohere.CohereEmbeddingClient.'
                'from_settings',
                return_value=embedder,
            ) as embedder_factory,
            mock.patch(
                'ai.core.integrations.attachment_search.AttachmentSearchProjection.'
                'from_settings',
                return_value=projection,
            ) as projection_factory,
        ):
            call_command('ingest_existing_attachments', '--limit', '1', stdout=out)
        report = out.getvalue()
        self.assertIn('INDEXED', report)
        self.assertEqual(
            AttachmentIngest.objects.filter(
                state=AttachmentIngestState.INDEXED
            ).count(),
            1,
        )
        # One construction each for the whole run (F-19), closed afterwards.
        self.assertEqual(embedder_factory.call_count, 1)
        self.assertEqual(projection_factory.call_count, 1)
        self.assertTrue(projection.closed)


class ClaimProtocolTests(RagFixtureTestCase):
    """F-04/F-06: atomic claim, cap-for-all-states, force, winner/loser."""

    def _seed_row(self, attachment, content=_MD, **overrides):
        """Create a registry row for the attachment's current content."""
        import hashlib

        defaults = {
            'attachment_id': attachment.pk,
            'model_type': attachment.model_type,
            'model_id': attachment.model_id,
            'source_sha256': hashlib.sha256(content).hexdigest(),
            'pipeline': 'doc',
        }
        defaults.update(overrides)
        return AttachmentIngest.objects.create(**defaults)

    def test_fresh_in_flight_twin_is_not_reclaimed(self):
        """A fresh EXTRACTING row means a twin owns it: no duplicate work."""
        attachment = _make_attachment('part', self.part.pk, 'twin.md', _MD)
        self._seed_row(
            attachment, state=AttachmentIngestState.EXTRACTING, attempts=1
        )
        row, embedder, projection = self._run(attachment.pk)
        self.assertEqual(embedder.calls, 0)
        self.assertEqual(projection.operations, [])
        self.assertEqual(row.state, AttachmentIngestState.EXTRACTING)
        self.assertEqual(row.attempts, 1)

    def test_stale_in_flight_row_is_taken_over(self):
        """Past the staleness horizon the claim succeeds (crash recovery)."""
        from datetime import timedelta

        from django.utils import timezone

        attachment = _make_attachment('part', self.part.pk, 'stale.md', _MD)
        seeded = self._seed_row(
            attachment, state=AttachmentIngestState.EXTRACTING, attempts=1
        )
        AttachmentIngest.objects.filter(pk=seeded.pk).update(
            updated_at=timezone.now() - timedelta(seconds=4000)
        )
        row, embedder, _projection = self._run(attachment.pk)
        self.assertEqual(embedder.calls, 1)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)
        self.assertEqual(row.attempts, 2)

    def test_attempts_cap_binds_in_every_state(self):
        """A stale EXTRACTING row at the cap is never re-claimed (F-04)."""
        from datetime import timedelta

        from django.utils import timezone

        attachment = _make_attachment('part', self.part.pk, 'capped.md', _MD)
        seeded = self._seed_row(
            attachment, state=AttachmentIngestState.EXTRACTING, attempts=3
        )
        AttachmentIngest.objects.filter(pk=seeded.pk).update(
            updated_at=timezone.now() - timedelta(seconds=4000)
        )
        row, embedder, _projection = self._run(attachment.pk)
        self.assertEqual(embedder.calls, 0)
        self.assertEqual(row.attempts, 3)

    def test_force_reingests_an_indexed_row(self):
        """force=True (backfill-only) re-runs a completed revision."""
        attachment = _make_attachment('part', self.part.pk, 'force.md', _MD)
        first, _e, _p = self._run(attachment.pk)
        self.assertEqual(first.state, AttachmentIngestState.INDEXED)
        again, embedder, _projection = self._run(attachment.pk, force=True)
        self.assertEqual(again.pk, first.pk)
        self.assertEqual(embedder.calls, 1)
        self.assertEqual(again.state, AttachmentIngestState.INDEXED)
        self.assertEqual(again.attempts, first.attempts + 1)

    def test_skipped_row_is_claimable_for_revival(self):
        """A SKIPPED row must re-ingest once routing says ingest (critic #2)."""
        attachment = _make_attachment('part', self.part.pk, 'revive2.md', _MD)
        self._seed_row(
            attachment,
            state=AttachmentIngestState.SKIPPED,
            error_code='ATTACHMENT_SKIP_PIPELINE_DISABLED',
        )
        row, embedder, _projection = self._run(attachment.pk)
        self.assertEqual(embedder.calls, 1)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)

    def test_fence_loss_walks_away_without_demoting(self):
        """Losing the fence mid-run writes nothing and deletes nothing."""
        attachment = _make_attachment('part', self.part.pk, 'fence.md', _MD)

        class TakeoverEmbedder(FakeEmbeddingClient):
            def embed_documents(self, texts):
                """Simulate a stale-takeover twin bumping the claim mid-run."""
                AttachmentIngest.objects.filter(
                    attachment_id=attachment.pk
                ).update(attempts=5)
                return super().embed_documents(texts)

        projection = FakeProjection()
        row, _e, _p = self._run(
            attachment.pk,
            embedding_client=TakeoverEmbedder(),
            projection=projection,
        )
        row.refresh_from_db()
        # The fenced INDEXED write must have matched zero rows: state stays
        # what the "twin" owns, and no purge/mark of any sha happened.
        self.assertNotEqual(row.state, AttachmentIngestState.INDEXED)
        self.assertEqual(
            [op[0] for op in projection.operations if op[0] != 'upsert'], []
        )

    def test_loser_purges_only_its_own_sha(self):
        """A newer-claimed peer wins; this run cleans up after itself only."""
        from datetime import timedelta

        from django.utils import timezone

        attachment = _make_attachment('part', self.part.pk, 'loser.md', _MD)
        peer = AttachmentIngest.objects.create(
            attachment_id=attachment.pk,
            model_type='part',
            model_id=self.part.pk,
            source_sha256='f' * 64,
            pipeline='doc',
            state=AttachmentIngestState.INDEXED,
            claimed_at=timezone.now() + timedelta(hours=1),
        )
        row, _embedder, projection = self._run(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.SUPERSEDED)
        self.assertEqual(
            projection.operations,
            [
                ('upsert', projection.operations[0][1]),
                ('mark_stale', attachment.pk, row.source_sha256),
                ('purge_sha', attachment.pk, row.source_sha256),
            ],
        )
        peer.refresh_from_db()
        self.assertEqual(peer.state, AttachmentIngestState.INDEXED)

    def test_indexed_short_circuit_renews_claim_clock(self):
        """A revert re-run renews claimed_at so the old revision outranks."""
        attachment = _make_attachment('part', self.part.pk, 'renew.md', _MD)
        first, _e, _p = self._run(attachment.pk)
        original_claim = first.claimed_at
        again, _e2, _p2 = self._run(attachment.pk)
        again.refresh_from_db()
        self.assertEqual(again.pk, first.pk)
        self.assertGreater(again.claimed_at, original_claim)


class RecordSkipGuardTests(RagFixtureTestCase):
    """F-10/B7: skips never erase failure history or clobber live runs."""

    def test_skip_preserves_failed_history(self):
        """A FAILED row later routed to a skip keeps its failure record."""
        import hashlib

        attachment = _make_attachment('part', self.part.pk, 'sheet.xlsx', _XLSX_HEAD)
        AttachmentIngest.objects.create(
            attachment_id=attachment.pk,
            model_type='part',
            model_id=self.part.pk,
            source_sha256=hashlib.sha256(_XLSX_HEAD).hexdigest(),
            pipeline='doc',
            state=AttachmentIngestState.FAILED,
            error_code='ATTACHMENT_EXTRACTION_FAILED',
            attempts=3,
        )
        row, _e, _p = self._run(attachment.pk)
        row.refresh_from_db()
        self.assertEqual(row.state, AttachmentIngestState.FAILED)
        self.assertEqual(row.error_code, 'ATTACHMENT_EXTRACTION_FAILED')

    def test_skip_never_clobbers_a_live_run(self):
        """An in-flight row survives a concurrent skip outcome."""
        import hashlib

        attachment = _make_attachment('part', self.part.pk, 'live.xlsx', _XLSX_HEAD)
        AttachmentIngest.objects.create(
            attachment_id=attachment.pk,
            model_type='part',
            model_id=self.part.pk,
            source_sha256=hashlib.sha256(_XLSX_HEAD).hexdigest(),
            pipeline='doc',
            state=AttachmentIngestState.EMBEDDING,
            attempts=1,
        )
        row, _e, _p = self._run(attachment.pk)
        row.refresh_from_db()
        self.assertEqual(row.state, AttachmentIngestState.EMBEDDING)


class SniffAndRouterTests(RagFixtureTestCase):
    """E1/F-21: window truncation, RIFF disambiguation, junk-prefixed PDFs."""

    def test_multibyte_straddling_window_is_text(self):
        """A UTF-8 char cut at the window must not become binary (E1)."""
        from aichat.services.attachment_ingestion import HEAD_BYTES, _sniff_kind

        head = ('a' * (HEAD_BYTES - 1) + 'é').encode('utf-8')[:HEAD_BYTES]
        self.assertEqual(_sniff_kind(head), 'text')

    def test_complete_small_file_with_dangling_multibyte_is_binary(self):
        """No trimming for heads shorter than the window."""
        from aichat.services.attachment_ingestion import _sniff_kind

        self.assertEqual(_sniff_kind('café'.encode()[:-1]), 'binary')

    def test_nonascii_markdown_ingests_end_to_end(self):
        """The E1 fix in vivo: a long non-ASCII manual indexes cleanly."""
        content = ('# Wartungshandbuch µm °C é\n\n' + 'Prüfen. ' * 400).encode('utf-8')
        attachment = _make_attachment('part', self.part.pk, 'wartung.md', content)
        row, _e, _p = self._run(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)

    def test_webp_sniffs_as_image_and_wave_as_audio(self):
        """RIFF containers disambiguate (F-21): WEBP≠video, WAV≠video."""
        from aichat.services.attachment_ingestion import _sniff_kind

        webp = b'RIFF\x00\x00\x00\x00WEBPVP8 ' + b'\x00' * 16
        wav = b'RIFF\x00\x00\x00\x00WAVEfmt ' + b'\x00' * 16
        avi = b'RIFF\x00\x00\x00\x00AVI LIST' + b'\x00' * 16
        self.assertEqual(_sniff_kind(webp), 'image')
        self.assertEqual(_sniff_kind(wav), 'audio')
        self.assertEqual(_sniff_kind(avi), 'video')

    def test_webp_part_upload_records_part_image_skip(self):
        """A WEBP on a part is an image skip, not a video misroute."""
        webp = b'RIFF\x00\x00\x00\x00WEBPVP8 ' + b'\x00' * 64
        attachment = _make_attachment('part', self.part.pk, 'photo.webp', webp)
        row, _e, _p = self._run(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.SKIPPED)
        self.assertEqual(row.error_code, 'ATTACHMENT_SKIP_PART_IMAGE')
        self.assertEqual(row.pipeline, 'image')

    def test_junk_prefixed_pdf_still_sniffs_as_pdf(self):
        """The %PDF- signature may sit up to 1024 bytes in (F-21)."""
        from aichat.services.attachment_ingestion import _sniff_kind

        junk_pdf = b'\x00' * 500 + b'%PDF-1.4\n'
        self.assertEqual(_sniff_kind(junk_pdf), 'pdf')


class OversizeTests(RagFixtureTestCase):
    """F-17: caps bind before the file occupies worker memory."""

    def test_doc_cap_skips_without_full_read(self):
        """An over-cap doc records its skip via streaming only."""
        attachment = _make_attachment('part', self.part.pk, 'big.md', _MD)
        Attachment.objects.filter(pk=attachment.pk).update(
            file_size=51 * 1024 * 1024
        )
        with mock.patch(
            'aichat.services.attachment_ingestion._read_attachment_bytes',
            side_effect=AssertionError('full read must not happen'),
        ):
            row, _e, _p = self._run(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.SKIPPED)
        self.assertEqual(row.error_code, 'ATTACHMENT_SKIP_DOC_OVERSIZE')

    def test_structural_cap_is_row_free(self):
        """Beyond the structural cap nothing is recorded at all."""
        from aichat.services.attachment_ingestion import structural_skip_reason

        attachment = _make_attachment('part', self.part.pk, 'huge.md', _MD)
        Attachment.objects.filter(pk=attachment.pk).update(
            file_size=501 * 1024 * 1024
        )
        attachment.refresh_from_db()
        with mock.patch(
            'ai.core.config.get_settings', return_value=_ai_settings()
        ):
            self.assertEqual(structural_skip_reason(attachment), 'oversize')
        row, _e, _p = self._run(attachment.pk)
        self.assertIsNone(row)


class WorkOrderOwnerTests(RagFixtureTestCase):
    """F-07: decision #10's recorded skips are reachable in production."""

    @override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True)
    def test_workorder_upload_offloads_for_recorded_skip(self):
        """WO uploads now offload so the router can record the skip."""
        from aichat import tasks as aichat_tasks

        with (
            mock.patch('InvenTree.tasks.offload_task', return_value=True) as off,
            mock.patch('ai.core.config.get_settings', return_value=_ai_settings()),
        ):
            Attachment.objects.create(
                model_type='workorder',
                model_id=777,
                attachment=SimpleUploadedFile('report.txt', b'service report\n'),
                comment='wo',
            )
        offloads = [
            call
            for call in off.call_args_list
            if call.args and call.args[0] is aichat_tasks.ingest_attachment
        ]
        self.assertGreaterEqual(len(offloads), 1)

    def test_workorder_doc_records_skip_row(self):
        """The task then records ATTACHMENT_SKIP_WORKORDER_DOC."""
        attachment = _make_attachment('workorder', 777, 'report.txt', b'notes\n')
        row, _e, _p = self._run(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.SKIPPED)
        self.assertEqual(row.error_code, 'ATTACHMENT_SKIP_WORKORDER_DOC')

    def test_repairpacket_records_skip_row(self):
        """Repair-packet artifacts record their exclusion (decision #10)."""
        attachment = _make_attachment('repairpacket', 55, 'packet.pdf', _PDF_HEAD)
        row, _e, _p = self._run(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.SKIPPED)
        self.assertEqual(row.error_code, 'ATTACHMENT_SKIP_REPAIRPACKET')

    def test_workorder_audio_is_terminal_unsupported(self):
        """WO/step audio records a TERMINAL unsupported-type skip (R3).

        Not a media-dark code: no pipeline (current or planned) ingests bare
        audio files, so a media-flag flip must never revive these stamps.
        """
        for model_type in ('workorder', 'workorderstepexecution'):
            with self.subTest(model_type=model_type):
                attachment = _make_attachment(
                    model_type, 778, 'note.wav', _WAV_HEAD
                )
                row, _e, _p = self._run(attachment.pk)
                self.assertEqual(row.state, AttachmentIngestState.SKIPPED)
                self.assertEqual(row.error_code, 'ATTACHMENT_SKIP_UNSUPPORTED_TYPE')
                self.assertEqual(row.pipeline, 'doc')

    def test_workorder_video_records_video_dark_skip(self):
        """WO/step video skips under the DISTINCT video-dark code (R3).

        Separate from the image code on purpose: image-flag flips must not
        revive video stamps before the R4 router change.
        """
        for model_type in ('workorder', 'workorderstepexecution'):
            with self.subTest(model_type=model_type):
                attachment = _make_attachment(
                    model_type, 779, 'clip.mp4', _MP4_HEAD
                )
                row, _e, _p = self._run(attachment.pk)
                self.assertEqual(row.state, AttachmentIngestState.SKIPPED)
                self.assertEqual(
                    row.error_code, 'ATTACHMENT_SKIP_VIDEO_PIPELINE_DARK'
                )
                self.assertEqual(row.pipeline, 'video')


class DocumentIntelligenceTests(RagFixtureTestCase):
    """The DI success leg (page mapping) and the value-free failure log."""

    def _fake_di_result(self):
        """Two-page markdown with DI-style page spans."""
        from types import SimpleNamespace

        page_one = '# Manual\n\n## Install\n\n' + 'Install steps. ' * 30
        page_two = '\n## Troubleshoot\n\n' + 'Check seals. ' * 30
        markdown = page_one + page_two
        return SimpleNamespace(
            content=markdown,
            pages=[
                SimpleNamespace(spans=[SimpleNamespace(offset=0)]),
                SimpleNamespace(spans=[SimpleNamespace(offset=len(page_one))]),
            ],
        )

    def test_di_success_maps_page_numbers(self):
        """DI markdown lands with per-section page numbers on chunks + docs."""
        result = self._fake_di_result()
        fake_client = mock.Mock(analyze_layout_markdown=mock.Mock(return_value=result))
        attachment = _make_attachment('part', self.part.pk, 'manual.pdf', _PDF_HEAD)
        with mock.patch(
            'ai.core.integrations.doc_intelligence.get_doc_intelligence_client',
            return_value=fake_client,
        ):
            row, _e, projection = self._run(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)
        self.assertEqual(row.extractor, 'di_layout')
        pages = {
            chunk.section_path: chunk.page_number
            for chunk in AttachmentChunk.objects.filter(ingest=row)
        }
        self.assertIn(1, pages.values())
        self.assertIn(2, pages.values())
        doc_pages = {doc['section_path']: doc['page_number'] for doc in projection.documents}
        self.assertEqual(pages, doc_pages)
        troubleshoot = [p for s, p in pages.items() if 'Troubleshoot' in s]
        self.assertEqual(troubleshoot, [2])

    def test_di_failure_log_is_value_free(self):
        """Provider text must never reach the log (F-13)."""
        failing = mock.Mock(
            analyze_layout_markdown=mock.Mock(
                side_effect=RuntimeError('secret-token-XYZZY in provider error')
            )
        )
        attachment = _make_attachment('part', self.part.pk, 'leak.pdf', _PDF_HEAD)
        with (
            mock.patch(
                'ai.core.integrations.doc_intelligence.get_doc_intelligence_client',
                return_value=failing,
            ),
            self.assertLogs('inventree', level='WARNING') as captured,
            self.assertRaises(AttachmentIngestionError),
        ):
            self._run(attachment.pk)
        rendered = '\n'.join(captured.output)
        self.assertIn('Document Intelligence extraction failed', rendered)
        self.assertNotIn('secret-token-XYZZY', rendered)
        self.assertIn('error_type=RuntimeError', rendered)


class PypdfRealTests(RagFixtureTestCase):
    """The explicit-override extractor against a real generated PDF."""

    def test_real_pypdf_extraction_with_page_map(self):
        """A reportlab two-page PDF extracts text and page starts."""
        try:
            from io import BytesIO

            from reportlab.pdfgen import canvas
        except ImportError:  # pragma: no cover - optional test dep
            self.skipTest('reportlab unavailable')
        buffer = BytesIO()
        pdf = canvas.Canvas(buffer)
        pdf.drawString(72, 720, 'Alpha page one content')
        pdf.showPage()
        pdf.drawString(72, 720, 'Beta page two content')
        pdf.showPage()
        pdf.save()

        from aichat.services.attachment_ingestion import _extract_with_pypdf

        text, page_starts = _extract_with_pypdf(buffer.getvalue())
        self.assertIn('Alpha page one content', text)
        self.assertIn('Beta page two content', text)
        self.assertEqual(len(page_starts), 2)
        self.assertEqual(page_starts[0], 0)


class SweepTests(RagFixtureTestCase):
    """The stale-resume sweep and orphan reconciliation."""

    @override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True)
    def test_stalled_row_is_reoffered(self):
        """Stranded in-flight rows below the cap get re-offered."""
        from datetime import timedelta

        from django.utils import timezone

        from aichat.services.attachment_ingestion import resume_stalled_ingests

        attachment = _make_attachment('part', self.part.pk, 'strand.md', _MD)
        row = AttachmentIngest.objects.create(
            attachment_id=attachment.pk,
            model_type='part',
            model_id=self.part.pk,
            source_sha256='a' * 64,
            pipeline='doc',
            state=AttachmentIngestState.EXTRACTING,
            attempts=1,
        )
        AttachmentIngest.objects.filter(pk=row.pk).update(
            updated_at=timezone.now() - timedelta(seconds=4000)
        )
        with (
            mock.patch('InvenTree.tasks.offload_task', return_value=True) as off,
            mock.patch('ai.core.config.get_settings', return_value=_ai_settings()),
        ):
            counts = resume_stalled_ingests()
        self.assertEqual(counts['resumed'], 1)
        self.assertTrue(
            any(call.args[1] == attachment.pk for call in off.call_args_list)
        )

    @override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True)
    def test_stalled_row_at_cap_is_terminalized(self):
        """At the cap a stranded row becomes FAILED/STALLED, not immortal."""
        from datetime import timedelta

        from django.utils import timezone

        from aichat.services.attachment_ingestion import resume_stalled_ingests

        attachment = _make_attachment('part', self.part.pk, 'dead.md', _MD)
        row = AttachmentIngest.objects.create(
            attachment_id=attachment.pk,
            model_type='part',
            model_id=self.part.pk,
            source_sha256='b' * 64,
            pipeline='doc',
            state=AttachmentIngestState.EMBEDDING,
            attempts=3,
        )
        AttachmentIngest.objects.filter(pk=row.pk).update(
            updated_at=timezone.now() - timedelta(seconds=4000)
        )
        with mock.patch('ai.core.config.get_settings', return_value=_ai_settings()):
            counts = resume_stalled_ingests()
        self.assertEqual(counts['stalled'], 1)
        row.refresh_from_db()
        self.assertEqual(row.state, AttachmentIngestState.FAILED)
        self.assertEqual(row.error_code, 'ATTACHMENT_INGEST_STALLED')

    def test_orphaned_rows_are_purged(self):
        """Rows whose attachment vanished are purged (denial ≡ nonexistence)."""
        from aichat.services.attachment_ingestion import reconcile_orphaned_ingests

        AttachmentIngest.objects.create(
            attachment_id=999999,
            model_type='part',
            model_id=self.part.pk,
            source_sha256='c' * 64,
            pipeline='doc',
            state=AttachmentIngestState.INDEXED,
        )
        self.assertEqual(reconcile_orphaned_ingests(dry_run=True), 1)
        projection = FakeProjection()
        purged = reconcile_orphaned_ingests(projection=projection)
        self.assertEqual(purged, 1)
        self.assertIn(('purge', 999999), projection.operations)
        row = AttachmentIngest.objects.get(attachment_id=999999)
        self.assertEqual(row.state, AttachmentIngestState.DELETED)


class RestampReceiverTests(RagFixtureTestCase):
    """§6.5 re-stamp receivers and the machine fan-out cost contract."""

    @override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True)
    def test_machine_part_change_offloads_restamp(self):
        """Linking a part with ingest rows offloads the part re-stamp."""
        from aichat import tasks as aichat_tasks

        AttachmentIngest.objects.create(
            attachment_id=12345,
            model_type='part',
            model_id=self.part_unlinked.pk,
            source_sha256='d' * 64,
            pipeline='doc',
            state=AttachmentIngestState.INDEXED,
        )
        with mock.patch('InvenTree.tasks.offload_task', return_value=True) as off:
            MachinePart.objects.create(
                machine=self.machine, part=self.part_unlinked
            )
        restamps = [
            call
            for call in off.call_args_list
            if call.args and call.args[0] is aichat_tasks.restamp_part_client_codes
        ]
        self.assertEqual(len(restamps), 1)
        self.assertEqual(restamps[0].args[1], self.part_unlinked.pk)

    @override_settings(AIMMS_ATTACHMENT_RAG_ENABLED=True)
    def test_machine_save_offloads_machine_restamp(self):
        """A machine save with linked parts offloads the machine re-stamp."""
        from aichat import tasks as aichat_tasks

        with mock.patch('InvenTree.tasks.offload_task', return_value=True) as off:
            self.machine.save()
        restamps = [
            call
            for call in off.call_args_list
            if call.args
            and call.args[0] is aichat_tasks.restamp_machine_client_codes
        ]
        self.assertEqual(len(restamps), 1)

    def test_machine_restamp_shares_projection_and_skips_noops(self):
        """No-op restamps build zero SearchClients (F-19)."""
        from aichat.services.attachment_ingestion import restamp_machine_client_codes

        attachment = _make_attachment(
            'assetmachine', self.machine.pk, 'm-doc.md', _MD
        )
        row, _e, _p = self._run(attachment.pk)
        self.assertEqual(row.client_codes, ['acme'])
        # Codes unchanged: no projection may be constructed at all.
        with mock.patch(
            'ai.core.integrations.attachment_search.AttachmentSearchProjection.'
            'from_settings',
            side_effect=AssertionError('no client for a no-op restamp'),
        ):
            touched = restamp_machine_client_codes(self.machine.pk)
        self.assertEqual(touched, 0)
        # Codes changed: exactly one shared projection serves machine + parts.
        AttachmentIngest.objects.filter(pk=row.pk).update(client_codes=['stale'])
        projection = FakeProjection()
        touched = restamp_machine_client_codes(
            self.machine.pk, projection=projection
        )
        self.assertEqual(touched, 1)
        self.assertEqual(
            projection.operations, [('merge', attachment.pk, ('acme',))]
        )


class TaskWrapperTests(RagFixtureTestCase):
    """The django-q wrappers delegate to the service functions."""

    def test_wrappers_delegate(self):
        """Each wrapper calls its service function with the given id."""
        from aichat import tasks as aichat_tasks

        with mock.patch(
            'aichat.services.attachment_ingestion.run_ingest', return_value=None
        ) as run:
            aichat_tasks.ingest_attachment(11)
        run.assert_called_once_with(11)
        with mock.patch(
            'aichat.services.attachment_ingestion.purge_attachment_artifacts',
            return_value=0,
        ) as purge:
            aichat_tasks.purge_attachment(12)
        purge.assert_called_once_with(12)
        with mock.patch(
            'aichat.services.attachment_ingestion.restamp_part_client_codes',
            return_value=0,
        ) as part_restamp:
            aichat_tasks.restamp_part_client_codes(13)
        part_restamp.assert_called_once_with(13)
        with mock.patch(
            'aichat.services.attachment_ingestion.restamp_machine_client_codes',
            return_value=0,
        ) as machine_restamp:
            aichat_tasks.restamp_machine_client_codes(14)
        machine_restamp.assert_called_once_with(14)

    def test_sweep_task_delegates(self):
        """The scheduled sweep calls the resume service."""
        from aichat import tasks as aichat_tasks

        with mock.patch(
            'aichat.services.attachment_ingestion.resume_stalled_ingests',
            return_value={'resumed': 0, 'stalled': 0, 'orphans': 0},
        ) as sweep:
            aichat_tasks.sweep_attachment_rag()
        sweep.assert_called_once_with()
