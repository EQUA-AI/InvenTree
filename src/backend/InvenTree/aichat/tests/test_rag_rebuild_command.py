"""R5 WP-D: rebuild_rag_index — round-trip fidelity is the whole contract.

Ingest through a recording projection, rebuild through a second, and the two
document lists must agree dict-for-dict INCLUDING vectors (fake vectors are
float32-exact, so pgvector round-trips them bit-identically, mirroring the
production PG float4 ↔ Edm.Single identity). Datetime strings are compared
as instants: the test runner flips USE_TZ off, so columns read back naive
while the live path wrote aware — the same trap WP-B documented.
"""

import hashlib
from datetime import UTC, datetime
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError

from aichat.models import AttachmentChunk, AttachmentIngest, AttachmentIngestState

from .test_attachment_rag_ingestion import (
    _MD,
    FakeProjection,
    _ai_settings,
    _make_attachment,
)
from .test_attachment_rag_media_ingestion import (
    _MP4,
    _PNG,
    MediaFixtureTestCase,
    _image_providers,
    _media_settings,
    _video_tool_fakes,
)

_INSTANT_KEYS = ('indexed_at', 'as_of', 'recorded_at', 'uploaded_at')


def _as_instant(value):
    """ISO string -> aware UTC datetime (naive assumed UTC — the trap)."""
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _normalized(doc):
    """Copy of a document with datetime strings canonicalized to instants."""
    out = dict(doc)
    for key in _INSTANT_KEYS:
        if out.get(key):
            out[key] = _as_instant(out[key]).isoformat()
    return out


class _VerifyProjection(FakeProjection):
    """Recording projection whose client() serves canned live documents."""

    def __init__(self, live_documents):
        super().__init__()
        self._live = list(live_documents)

    def client(self):
        live = self._live

        class _Stub:
            def search(self, **kwargs):
                return list(live)

        return _Stub()


class RebuildTextTests(MediaFixtureTestCase):
    """Text-space rebuild against the recorded live documents."""

    def _rebuild(self, projection, *args, settings=None):
        out = StringIO()
        with (
            mock.patch(
                'ai.core.config.get_settings',
                # The fakes stamp their own model name; align the configured
                # one so the drift guard (tested separately) stays quiet.
                return_value=settings or _ai_settings(COHERE_EMBED_MODEL='fake-cohere'),
            ),
            mock.patch(
                'ai.core.integrations.attachment_search.AttachmentSearchProjection.'
                'from_settings',
                return_value=projection,
            ),
        ):
            call_command('rebuild_rag_index', '--space', 'text', *args, stdout=out)
        return out.getvalue()

    def test_text_round_trip_is_exact(self):
        """Rebuilt text documents equal the live ones, vectors included."""
        attachment = _make_attachment('part', self.part.pk, 'manual.md', _MD)
        row, _embedder, live = self._run(attachment.pk)
        self.assertEqual(row.state, AttachmentIngestState.INDEXED)
        rebuilt = FakeProjection()
        self._rebuild(rebuilt)
        self.assertEqual(len(rebuilt.documents), len(live.documents))
        for built, original in zip(rebuilt.documents, live.documents, strict=True):
            self.assertEqual(_normalized(built), _normalized(original))
        self.assertTrue(rebuilt.closed)

    def test_chunk_gap_refuses(self):
        """A missing chunk_index re-keys every later doc — hard refusal."""
        attachment = _make_attachment('part', self.part.pk, 'manual.md', _MD)
        row, _embedder, _live = self._run(attachment.pk)
        AttachmentChunk.objects.filter(ingest=row, chunk_index=0).delete()
        self.assertTrue(row.chunks.exists())
        with self.assertRaises(CommandError):
            self._rebuild(FakeProjection())

    def test_unstamped_row_refuses(self):
        """indexed_at IS NULL means the 0031 repair has not run — refuse."""
        attachment = _make_attachment('part', self.part.pk, 'manual.md', _MD)
        self._run(attachment.pk)
        AttachmentIngest.objects.update(indexed_at=None)
        with self.assertRaises(CommandError):
            self._rebuild(FakeProjection())

    def test_non_winner_is_skipped_not_projected(self):
        """Only the _claim_order winner is projected; peers are counted."""
        attachment = _make_attachment('part', self.part.pk, 'manual.md', _MD)
        row, _embedder, live = self._run(attachment.pk)
        loser = AttachmentIngest.objects.create(
            attachment_id=row.attachment_id,
            model_type=row.model_type,
            model_id=row.model_id,
            source_sha256=hashlib.sha256(b'old-content').hexdigest(),
            pipeline='doc',
            state=AttachmentIngestState.INDEXED,
            indexed_at=datetime(2026, 1, 1, 0, 0, 0),
            claimed_at=datetime(2026, 1, 1, 0, 0, 0),
            embedding_model='fake-cohere',
            embedding_dimensions=1536,
        )
        rebuilt = FakeProjection()
        output = self._rebuild(rebuilt)
        self.assertIn('superseded_by_peer', output)
        self.assertEqual(len(rebuilt.documents), len(live.documents))
        self.assertNotIn(loser.source_sha256[:12], str(rebuilt.documents))

    def test_dimension_drift_is_a_hard_error(self):
        """A row stamped with foreign dimensions cannot be rebuilt."""
        attachment = _make_attachment('part', self.part.pk, 'manual.md', _MD)
        self._run(attachment.pk)
        AttachmentIngest.objects.update(embedding_dimensions=3072)
        with self.assertRaises(CommandError):
            self._rebuild(FakeProjection())

    def test_model_drift_requires_the_override(self):
        """Model drift refuses without --allow-model-drift, proceeds with."""
        attachment = _make_attachment('part', self.part.pk, 'manual.md', _MD)
        self._run(attachment.pk)
        AttachmentIngest.objects.update(embedding_model='retired-model')
        with self.assertRaises(CommandError):
            self._rebuild(FakeProjection())
        rebuilt = FakeProjection()
        output = self._rebuild(rebuilt, '--allow-model-drift')
        self.assertIn('rebuilt', output)
        # The ROW's model is what gets projected, never the configured one.
        self.assertEqual(rebuilt.documents[0]['embedding_model'], 'retired-model')

    def test_inflight_ingest_refuses_without_allow_live(self):
        """EXTRACTING/EMBEDDING rows mean the env is not quiesced."""
        attachment = _make_attachment('part', self.part.pk, 'manual.md', _MD)
        self._run(attachment.pk)
        AttachmentIngest.objects.create(
            attachment_id=99999,
            model_type='part',
            model_id=1,
            source_sha256=hashlib.sha256(b'x').hexdigest(),
            pipeline='doc',
            state=AttachmentIngestState.EXTRACTING,
        )
        with self.assertRaises(CommandError):
            self._rebuild(FakeProjection())
        rebuilt = FakeProjection()
        self._rebuild(rebuilt, '--allow-live')
        self.assertTrue(rebuilt.documents)

    def test_verify_mode_reports_equal_against_live(self):
        """--verify diffs the rebuilt docs against the served live ones."""
        attachment = _make_attachment('part', self.part.pk, 'manual.md', _MD)
        _row, _embedder, live = self._run(attachment.pk)

        # Serve the live docs with the datetime shape the rebuild will emit:
        # production compares aware-vs-aware, but the test runner reads the
        # columns back NAIVE (USE_TZ off), so the built strings are naive.
        def _as_read_back(doc):
            out = dict(doc)
            for key in ('indexed_at', 'as_of'):
                out[key] = _as_instant(out[key]).replace(tzinfo=None).isoformat()
            return out

        projection = _VerifyProjection([_as_read_back(d) for d in live.documents])
        output = self._rebuild(projection, '--verify')
        self.assertIn('verified_equal', output)
        self.assertEqual(projection.documents, [])  # verify writes nothing


class RebuildSafetyTests(MediaFixtureTestCase):
    """R5 review fixes: blank scope, deletion race, count mismatch, Z-verify."""

    def _rebuild(self, projection, *args, settings=None):
        out = StringIO()
        with (
            mock.patch(
                'ai.core.config.get_settings',
                return_value=settings or _ai_settings(COHERE_EMBED_MODEL='fake-cohere'),
            ),
            mock.patch(
                'ai.core.integrations.attachment_search.AttachmentSearchProjection.'
                'from_settings',
                return_value=projection,
            ),
        ):
            call_command('rebuild_rag_index', '--space', 'text', *args, stdout=out)
        return out.getvalue()

    def test_blank_scope_key_refuses_before_any_work(self):
        """A mis-sourced environment must never blank scope_key corpus-wide."""
        _make_attachment('part', self.part.pk, 'manual.md', _MD)
        self._run(_make_attachment('part', self.part.pk, 'other.md', _MD).pk)
        with self.assertRaisesRegex(CommandError, 'SINGLE_SITE_POLICY_KEY'):
            self._rebuild(
                FakeProjection(),
                settings=_ai_settings(
                    COHERE_EMBED_MODEL='fake-cohere', single_site_policy_key=''
                ),
            )

    def test_deleted_attachment_mid_rebuild_is_purged_not_resurrected(self):
        """The deletion race belt: purge lands between build and upsert."""
        from common.models import Attachment

        attachment = _make_attachment('part', self.part.pk, 'manual.md', _MD)
        row, _embedder, _live = self._run(attachment.pk)

        class _DeletesUnderneath(FakeProjection):
            def upsert_documents(self, documents):
                super().upsert_documents(documents)
                # Simulate purge_attachment_artifacts interleaving: rows
                # tombstone DELETED while the command holds built documents.
                AttachmentIngest.objects.filter(pk=row.pk).update(
                    state=AttachmentIngestState.DELETED
                )
                Attachment.objects.filter(pk=attachment.pk).delete()

        projection = _DeletesUnderneath()
        output = self._rebuild(projection)
        self.assertIn('purged_after_delete', output)
        ops = [op[0] for op in projection.operations]
        self.assertEqual(ops, ['upsert', 'mark_stale', 'purge_sha'])

    def test_chunk_count_mismatch_refuses(self):
        """A lost TAIL chunk keeps indices contiguous; the count catches it."""
        attachment = _make_attachment('part', self.part.pk, 'manual.md', _MD)
        row, _embedder, _live = self._run(attachment.pk)
        AttachmentIngest.objects.filter(pk=row.pk).update(
            chunk_count=row.chunks.count() + 1
        )
        with self.assertRaises(CommandError):
            self._rebuild(FakeProjection())

    def test_verify_accepts_azure_z_normalized_datetimes(self):
        """Azure returns Edm.DateTimeOffset with a Z suffix; instants match."""
        attachment = _make_attachment('part', self.part.pk, 'manual.md', _MD)
        _row, _embedder, live = self._run(attachment.pk)

        def _as_azure(doc):
            out = dict(doc)
            for key in ('indexed_at', 'as_of', 'uploaded_at'):
                if out.get(key):
                    out[key] = _as_instant(out[key]).isoformat().replace('+00:00', 'Z')
            return out

        projection = _VerifyProjection([_as_azure(d) for d in live.documents])
        output = self._rebuild(projection, '--verify')
        self.assertIn('verified_equal', output)


class RebuildMediaTests(MediaFixtureTestCase):
    """Media-space rebuild: image id preservation and video round-trip."""

    def _rebuild(self, projection, *args):
        out = StringIO()
        with (
            mock.patch(
                'ai.core.config.get_settings',
                return_value=_media_settings(GEMINI_EMBED_MODEL='fake-gemini'),
            ),
            mock.patch(
                'ai.core.integrations.attachment_search.MediaSearchProjection.'
                'from_settings',
                return_value=projection,
            ),
        ):
            call_command('rebuild_rag_index', '--space', 'media', *args, stdout=out)
        return out.getvalue()

    def test_image_round_trip_preserves_img_id(self):
        """PG stores segment_index=0; the rebuilt doc key must stay '-img'."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'nameplate.png', _PNG
        )
        with _image_providers():
            row, _embedder, live = self._run_media(attachment.pk)
        rebuilt = FakeProjection()
        self._rebuild(rebuilt)
        self.assertEqual(len(rebuilt.documents), 1)
        self.assertTrue(rebuilt.documents[0]['id'].endswith('-img'))
        self.assertEqual(
            _normalized(rebuilt.documents[0]), _normalized(live.documents[0])
        )
        self.assertEqual(row.pipeline, 'image')

    def test_video_round_trip_is_exact(self):
        """All three video segments rebuild dict-for-dict identical."""
        attachment = _make_attachment(
            'workorder', self.work_order.pk, 'seal-swap.mp4', _MP4
        )
        with _image_providers(), _video_tool_fakes():
            _row, _embedder, live = self._run_media(attachment.pk)
        rebuilt = FakeProjection()
        self._rebuild(rebuilt)
        self.assertEqual(len(rebuilt.documents), 3)
        for built, original in zip(rebuilt.documents, live.documents, strict=True):
            self.assertEqual(_normalized(built), _normalized(original))
