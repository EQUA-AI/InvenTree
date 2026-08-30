"""Local ffmpeg/ffprobe orchestration for the video ingest path (R4).

Lives beside ``attachment_ingestion`` (not in ``ai/core/integrations``): the
integrations layer is for remote providers; ffmpeg is a local binary used
exclusively by the ingestion service and the aichat boot probe.

Hard rules: every call is list-argv ``subprocess.run`` with an explicit
``timeout=``; return codes are checked manually (never ``check=True``, so no
``CalledProcessError`` carrying provider output is ever constructed); and
ffmpeg/ffprobe stderr is NEVER logged, stored, or chained — it can embed the
uploader-authored file path and arbitrary container metadata. Failures log a
static message via ``log_fault`` and raise ``VideoToolError`` ``from None``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime

from ai.core.faults import log_fault

logger = logging.getLogger('inventree')

PROBE_TIMEOUT_S = 60
CUT_TIMEOUT_S = 120
KEYFRAME_TIMEOUT_S = 60

_FFMPEG_BASE = ('-nostdin', '-hide_banner', '-loglevel', 'error')


class VideoToolError(Exception):
    """A bounded local-video-tool failure carrying only a value-free code."""

    code = 'ATTACHMENT_VIDEO_TOOL_FAILED'

    def __init__(self, message: str, *, code: str | None = None) -> None:
        """Attach an optional override for the class-level default code."""
        super().__init__(message)
        if code is not None:
            self.code = code


def ffmpeg_available() -> bool:
    """Whether both required binaries are on PATH (boot probe + preflight)."""
    return bool(shutil.which('ffmpeg')) and bool(shutil.which('ffprobe'))


def _run(
    argv: list[str], *, timeout: int, event: str, code: str
) -> subprocess.CompletedProcess:
    """Run one tool invocation under the module's value-free contract."""
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        raise VideoToolError(
            'ffmpeg/ffprobe is not installed', code='ATTACHMENT_VIDEO_TOOL_UNAVAILABLE'
        ) from None
    except subprocess.TimeoutExpired as exc:
        log_fault(logger, event, exc, stage='attachment_video', level=logging.WARNING)
        raise VideoToolError(event, code=code) from None
    except Exception as exc:
        log_fault(logger, event, exc, stage='attachment_video', level=logging.WARNING)
        raise VideoToolError(event, code=code) from None
    if completed.returncode != 0:
        # Deliberately no exception object: stderr/stdout stay unlogged.
        logger.warning('%s (stage=attachment_video rc=%s)', event, completed.returncode)
        raise VideoToolError(event, code=code)
    return completed


@dataclass(frozen=True)
class VideoProbe:
    """The container facts the pipeline needs; nothing attacker-raw leaves."""

    duration_s: float
    recorded_at: datetime | None
    width: int | None
    height: int | None
    has_video_stream: bool


def _parse_recorded_at(raw: object) -> datetime | None:
    """Container ``creation_time`` → aware datetime, or None (never raw)."""
    try:
        text = str(raw or '').strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except Exception:
        return None


def probe_video(path: str) -> VideoProbe:
    """Ffprobe one source file; malformed output fails value-free."""
    completed = _run(
        [
            'ffprobe',
            '-v',
            'error',
            '-print_format',
            'json',
            '-show_format',
            '-show_streams',
            path,
        ],
        timeout=PROBE_TIMEOUT_S,
        event='Video probe failed',
        code='ATTACHMENT_VIDEO_PROBE_FAILED',
    )
    try:
        payload = json.loads(completed.stdout or b'{}')
        duration_s = float(payload.get('format', {}).get('duration'))
        if not duration_s > 0:
            raise ValueError('non-positive duration')
    except Exception as exc:
        log_fault(
            logger,
            'Video probe output was malformed',
            exc,
            stage='attachment_video',
            level=logging.WARNING,
        )
        raise VideoToolError(
            'Video probe output was malformed', code='ATTACHMENT_VIDEO_PROBE_FAILED'
        ) from None
    width = height = None
    has_video_stream = False
    for stream in payload.get('streams') or []:
        if stream.get('codec_type') == 'video':
            has_video_stream = True
            try:
                width = int(stream.get('width'))
                height = int(stream.get('height'))
            except Exception:
                width = height = None
            break
    recorded_at = _parse_recorded_at(
        (payload.get('format', {}).get('tags') or {}).get('creation_time')
    )
    return VideoProbe(
        duration_s=duration_s,
        recorded_at=recorded_at,
        width=width,
        height=height,
        has_video_stream=has_video_stream,
    )


def plan_segments(
    duration_s: float, *, window_s: int, overlap_s: int
) -> list[tuple[float, float]]:
    """Fixed windows with overlap; every clip <= window_s (the Gemini cap).

    A window is emitted only when it contributes content beyond the previous
    window's end, so the plan never contains a segment fully inside its
    predecessor; the final window clamps to the duration. duration<=window
    yields exactly one [(0, duration)] window.
    """
    step = window_s - overlap_s  # config validator guarantees > 0
    windows: list[tuple[float, float]] = []
    index = 0
    while True:
        start = float(index * step)
        if index > 0 and start + overlap_s >= duration_s:
            break
        end = min(start + window_s, duration_s)
        windows.append((start, end))
        if end >= duration_s:
            break
        index += 1
    return windows


def cut_segment(path: str, start_s: float, duration_s: float, out_path: str) -> None:
    """Stream-copy one clip (``-ss`` before ``-i``: fast keyframe-snapped seek).

    The clip is an embedding input only — stored timecodes are the NOMINAL
    plan against the original timeline, so a few keyframe-snap seconds of
    lead-in are noise, not error. Re-encoding is unaffordable at 1 vCPU.
    """
    _run(
        [
            'ffmpeg',
            *_FFMPEG_BASE,
            '-ss',
            f'{start_s:.3f}',
            '-i',
            path,
            '-t',
            f'{duration_s:.3f}',
            '-map',
            '0:v:0',
            '-map',
            '0:a:0?',
            '-dn',
            '-sn',
            '-c',
            'copy',
            '-y',
            out_path,
        ],
        timeout=CUT_TIMEOUT_S,
        event='Video segment cut failed',
        code='ATTACHMENT_VIDEO_SEGMENT_FAILED',
    )


def extract_keyframe(path: str, at_s: float, out_path: str) -> None:
    """One JPEG frame at ``at_s`` (bounded quality/size for OCR + display)."""
    _run(
        [
            'ffmpeg',
            *_FFMPEG_BASE,
            '-ss',
            f'{at_s:.3f}',
            '-i',
            path,
            '-frames:v',
            '1',
            '-vf',
            "scale='min(1280,iw)':-2",
            '-q:v',
            '4',
            '-y',
            out_path,
        ],
        timeout=KEYFRAME_TIMEOUT_S,
        event='Video keyframe extraction failed',
        code='ATTACHMENT_VIDEO_KEYFRAME_FAILED',
    )


def extract_frames(path: str, count: int, out_dir: str) -> list[str]:
    """Evenly sample ``count`` JPEG frames across ``path`` in ONE ffmpeg run.

    Caption input only. Deliberately smaller than :func:`extract_keyframe`
    (640 px vs 1280 px, lower quality): these ride a vision call at
    ``detail:"low"``, while OCR keeps the untouched full-resolution midpoint
    keyframe. Frames land in the caller's temp workdir and are never persisted
    or served -- ``thumbnail_path`` still points at the midpoint keyframe.

    Returns the written paths in time order; a short read (ffmpeg produced
    fewer frames than asked, e.g. a very short clip) is a legitimate outcome
    and the caller captions whatever came back.
    """
    if count < 1:
        raise VideoToolError(
            'Frame count must be positive', code='ATTACHMENT_VIDEO_FRAMES_FAILED'
        )
    pattern = os.path.join(out_dir, 'frame-%03d.jpg')
    _run(
        [
            'ffmpeg',
            *_FFMPEG_BASE,
            '-i',
            path,
            '-vf',
            "thumbnail,scale='min(640,iw)':-2",
            '-frames:v',
            str(count),
            '-q:v',
            '6',
            '-y',
            pattern,
        ],
        timeout=KEYFRAME_TIMEOUT_S,
        event='Video frame sampling failed',
        code='ATTACHMENT_VIDEO_FRAMES_FAILED',
    )
    written = sorted(
        os.path.join(out_dir, name)
        for name in os.listdir(out_dir)
        if name.startswith('frame-') and name.endswith('.jpg')
    )
    if not written:
        raise VideoToolError(
            'Video frame sampling produced no frames',
            code='ATTACHMENT_VIDEO_FRAMES_FAILED',
        )
    return written


__all__ = [
    'CUT_TIMEOUT_S',
    'KEYFRAME_TIMEOUT_S',
    'PROBE_TIMEOUT_S',
    'VideoProbe',
    'VideoToolError',
    'cut_segment',
    'extract_frames',
    'extract_keyframe',
    'ffmpeg_available',
    'plan_segments',
    'probe_video',
]
