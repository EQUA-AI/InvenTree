"""R5 WP-0a: settle what ``gemini-embedding-2`` actually honours, live.

Diagnostic only — this command never writes a registry row, never touches an
index, and never changes configuration. It exists because two research passes
disagreed about how Gemini Embedding 2 is conditioned for asymmetric retrieval
(a ``task_type`` parameter vs literal string prefixes), and because the SDK
cannot answer that question: ``EmbedContentConfig`` will happily *send*
``taskType``, but only the service decides whether it is honoured, ignored, or
rejected.

The decisive output is the cosine block. If ``cos(baseline, task_type)`` is
~1.0 then ``taskType`` is accepted-but-ignored — the likeliest trap, and the
one that kills the WP-2a change for the price of one API call. If instead the
prefix cell diverges, the literal-prefix hypothesis is the real mechanism.

Cells 6-9 answer the audio question the same way: an ``AUDIO`` entry in
``statistics.tokens_details`` is the only proof that ``audio_track_extraction``
is not a silent no-op. Note the attribute is snake_case after SDK coercion;
reading ``.tokensDetails`` returns ``None`` and would look like a negative.

Nothing here is a gate on its own. It produces the evidence that the WP-2
config work is then written against.
"""

from __future__ import annotations

import math

from django.core.management.base import BaseCommand, CommandError

#: Vertex samples video at 1 FPS; a 60 s window is the shipped segment size.
_PROBE_TEXT = 'influent pump mechanical seal replacement procedure'
_PREFIX_QUERY = 'task: search result | query: '


def _cosine(left: list[float], right: list[float]) -> float:
    """Cosine similarity between two equal-width vectors."""
    if not left or not right or len(left) != len(right):
        return float('nan')
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return float('nan')
    return dot / (left_norm * right_norm)


class Command(BaseCommand):
    """Probe the live Gemini embedding surface; print, never mutate."""

    help = (
        'Diagnostic matrix against the pinned Gemini embedding model: does it '
        'honour task_type, do literal prefixes matter, and does '
        'audio_track_extraction actually fuse an audio track? Writes nothing.'
    )

    def add_arguments(self, parser):
        """Register the optional media inputs (text cells always run)."""
        parser.add_argument(
            '--image',
            default='',
            help='Path to a small PNG/JPEG for the image cells; skipped if unset',
        )
        parser.add_argument(
            '--video',
            default='',
            help='Path to an MP4 for the audio cells; skipped if unset',
        )
        parser.add_argument(
            '--segment-s',
            type=int,
            default=60,
            help='Seconds to cut from --video for the audio cells (default 60)',
        )

    def handle(self, *args, **options):
        """Run the matrix and print one line per cell plus the cosine block."""
        from ai.core.integrations.embeddings_gemini import (
            GeminiEmbeddingClient,
            MediaEmbeddingError,
            _genai_types,
        )

        try:
            wrapper = GeminiEmbeddingClient.from_settings()
        except MediaEmbeddingError as exc:
            raise CommandError(f'Gemini client unavailable ({exc.code})') from exc

        types = _genai_types()
        client = wrapper._get_client()
        model = wrapper.model
        dimensions = wrapper.dimensions

        # --- Routing assertions -------------------------------------------------
        # On the PREDICT path the SDK silently DROPS audio_track_extraction and
        # document_ocr, so every audio result below would be a false negative.
        # Assert the dispatch instead of assuming it.
        vertexai = bool(getattr(client.models._api_client, 'vertexai', False))
        self.stdout.write(f'model            = {model} ({dimensions} dims)')
        self.stdout.write(f'vertexai         = {vertexai}')
        try:
            from google.genai import _transformers as gt

            embed_content_path = bool(gt.t_is_vertex_embed_content_model(model))
        except Exception:
            embed_content_path = None
        self.stdout.write(f'EMBED_CONTENT    = {embed_content_path}')
        if not vertexai:
            self.stdout.write(
                self.style.WARNING('NOT on Vertex: audio cells cannot be trusted')
            )
        if embed_content_path is False:
            self.stdout.write(
                self.style.ERROR(
                    'PREDICT path: audio_track_extraction is silently dropped'
                )
            )
        self.stdout.write('')

        vectors: dict[str, list[float]] = {}

        def run(label: str, contents, **config_kwargs) -> None:
            """Embed one cell, print its outcome, and stash the vector."""
            config = types.EmbedContentConfig(
                output_dimensionality=dimensions, **config_kwargs
            )
            try:
                response = client.models.embed_content(
                    model=model, contents=contents, config=config
                )
            except Exception as exc:
                # Value-free: provider errors can carry tokens/credentials, so
                # only the exception class reaches the transcript.
                self.stdout.write(f'{label:<26} ERROR {type(exc).__name__}')
                return
            embeddings = getattr(response, 'embeddings', None) or []
            if not embeddings:
                self.stdout.write(f'{label:<26} EMPTY')
                return
            first = embeddings[0]
            values = list(getattr(first, 'values', None) or [])
            vectors[label] = values
            stats = getattr(first, 'statistics', None)
            detail = ''
            if stats is not None:
                # snake_case after SDK coercion; .tokensDetails returns None.
                modalities = {
                    str(getattr(entry, 'modality', '?')): getattr(
                        entry, 'token_count', 0
                    )
                    for entry in (getattr(stats, 'tokens_details', None) or [])
                }
                detail = (
                    f' tokens={getattr(stats, "token_count", "?")}'
                    f' truncated={getattr(stats, "truncated", "?")}'
                    f' modalities={modalities or "{}"}'
                )
            self.stdout.write(
                f'{label:<26} OK n={len(embeddings)} w={len(values)}{detail}'
            )

        # --- Text cells 1-4 -----------------------------------------------------
        run('1 text baseline', _PROBE_TEXT)
        run('2 text task_type=QUERY', _PROBE_TEXT, task_type='RETRIEVAL_QUERY')
        run(
            '3 text task_type=DOC',
            _PROBE_TEXT,
            task_type='RETRIEVAL_DOCUMENT',
            title='Influent pump manual',
        )
        run('4 text literal prefix', f'{_PREFIX_QUERY}{_PROBE_TEXT}')

        # --- Image cell 5 -------------------------------------------------------
        image_path = options['image']
        if image_path:
            try:
                with open(image_path, 'rb') as handle:
                    image_bytes = handle.read()
            except OSError as exc:
                raise CommandError(
                    f'--image unreadable ({type(exc).__name__})'
                ) from exc
            mime = 'image/png' if image_path.lower().endswith('.png') else 'image/jpeg'
            part = types.Part.from_bytes(data=image_bytes, mime_type=mime)
            run('5a image baseline', part)
            run('5b image task_type', part, task_type='RETRIEVAL_DOCUMENT')
        else:
            self.stdout.write('5  image                  SKIPPED (--image unset)')

        # --- Audio cells 6-9 ----------------------------------------------------
        video_path = options['video']
        if video_path:
            import os
            import tempfile

            from aichat.services import video_tools

            with tempfile.TemporaryDirectory() as workdir:
                clip = os.path.join(workdir, 'probe.mp4')
                try:
                    video_tools.cut_segment(video_path, 0.0, options['segment_s'], clip)
                    with open(clip, 'rb') as handle:
                        clip_bytes = handle.read()
                except Exception as exc:
                    raise CommandError(
                        f'--video segment failed ({type(exc).__name__})'
                    ) from exc
                clip_part = types.Part.from_bytes(
                    data=clip_bytes, mime_type='video/mp4'
                )
                run('6 video audio=off', clip_part, audio_track_extraction=False)
                run('7 video audio=ON', clip_part, audio_track_extraction=True)
                run(
                    '8 video audio+notrunc',
                    clip_part,
                    audio_track_extraction=True,
                    auto_truncate=False,
                )
                # Cell 9: deliberately oversized, to capture the error surface
                # that WP-2b must terminalize rather than retry.
                try:
                    long_clip = os.path.join(workdir, 'probe-long.mp4')
                    video_tools.cut_segment(video_path, 0.0, 120, long_clip)
                    with open(long_clip, 'rb') as handle:
                        long_bytes = handle.read()
                    run(
                        '9 video oversized',
                        types.Part.from_bytes(data=long_bytes, mime_type='video/mp4'),
                        audio_track_extraction=True,
                        auto_truncate=False,
                    )
                except Exception as exc:
                    self.stdout.write(
                        f'9 video oversized          SETUP {type(exc).__name__}'
                    )
        else:
            self.stdout.write('6-9 video                SKIPPED (--video unset)')

        # --- The cosine block: this is what actually decides WP-2a --------------
        self.stdout.write('')
        base = vectors.get('1 text baseline')
        for label in (
            '2 text task_type=QUERY',
            '3 text task_type=DOC',
            '4 text literal prefix',
        ):
            other = vectors.get(label)
            if base and other:
                self.stdout.write(f'cos(1, {label:<24}) = {_cosine(base, other):.6f}')
        self.stdout.write('')
        self.stdout.write(
            'Read: cos(1,2) ~= 1.0 means taskType is accepted but IGNORED -> WP-2a '
            'must use prefixes or be dropped. An AUDIO entry in cell 7 modalities '
            'is the only proof audio_track_extraction is not a no-op.'
        )
