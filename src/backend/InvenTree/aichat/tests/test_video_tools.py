"""R4 video tools: window math, ffprobe parsing, argv pins, value-free failure."""

import itertools
import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from PIL import Image, ImageStat

from aichat.services.attachment_ingestion import (
    AttachmentIngestionError,
    _read_video_segment_bytes,
)
from aichat.services.video_tools import (
    CUT_TIMEOUT_S,
    KEYFRAME_TIMEOUT_S,
    PROBE_TIMEOUT_S,
    VideoToolError,
    cut_segment,
    extract_keyframe,
    ffmpeg_available,
    plan_segments,
    probe_video,
)

_RUN = 'aichat.services.video_tools.subprocess.run'
_WHICH = 'aichat.services.video_tools.shutil.which'


def _completed(returncode=0, stdout=b'', stderr=b''):
    """One fake ffmpeg/ffprobe process result."""
    return subprocess.CompletedProcess(
        args=['fake'], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _ffprobe_json(payload):
    """A successful ffprobe run whose stdout carries the given JSON payload."""
    return _completed(stdout=json.dumps(payload).encode())


class PlanSegmentsTests(SimpleTestCase):
    """The fixed-window plan the whole video pipeline hangs off."""

    def test_600s_yields_exactly_eleven_windows(self):
        """600 s at 60/5 plans 11 windows ending at (550, 600)."""
        windows = plan_segments(600.0, window_s=60, overlap_s=5)
        self.assertEqual(len(windows), 11)
        self.assertEqual(windows[0], (0.0, 60.0))
        self.assertEqual(windows[-1], (550.0, 600.0))

    def test_130s_yields_three_nominal_windows(self):
        """The eval-fixture duration pins the exact three-window plan."""
        self.assertEqual(
            plan_segments(130.0, window_s=60, overlap_s=5),
            [(0.0, 60.0), (55.0, 115.0), (110.0, 130.0)],
        )

    def test_duration_at_or_below_window_is_a_single_window(self):
        """A duration at or below the window collapses to [(0, duration)]."""
        self.assertEqual(plan_segments(45.0, window_s=60, overlap_s=5), [(0.0, 45.0)])
        self.assertEqual(plan_segments(60.0, window_s=60, overlap_s=5), [(0.0, 60.0)])

    def test_just_over_window_yields_two_windows(self):
        """65 s needs a second window for the 5 s tail beyond the first."""
        self.assertEqual(
            plan_segments(65.0, window_s=60, overlap_s=5), [(0.0, 60.0), (55.0, 65.0)]
        )

    def test_900s_duration_cap_yields_seventeen_windows(self):
        """The config duration cap (900 s) bounds the plan at 17 segments."""
        windows = plan_segments(900.0, window_s=60, overlap_s=5)
        self.assertEqual(len(windows), 17)
        self.assertEqual(windows[-1], (880.0, 900.0))

    def test_every_window_fits_the_gemini_cap_and_covers_the_timeline(self):
        """No window exceeds window_s; the plan tiles [0, duration] with overlap."""
        for duration in (1.0, 59.9, 61.0, 115.0, 130.0, 247.3, 600.0, 900.0):
            with self.subTest(duration=duration):
                windows = plan_segments(duration, window_s=60, overlap_s=5)
                self.assertEqual(windows[0][0], 0.0)
                self.assertEqual(windows[-1][1], duration)
                for start, end in windows:
                    self.assertLessEqual(end - start, 60)
                    self.assertGreater(end, start)
                for (_a_start, a_end), (b_start, b_end) in itertools.pairwise(windows):
                    self.assertLess(b_start, a_end)  # contiguous with overlap
                    self.assertGreater(b_end, a_end)  # never fully contained


class CommittedFixtureTests(SimpleTestCase):
    """The frozen golden video must preserve its midpoint OCR anchors."""

    def test_midpoint_keyframes_match_the_three_planned_scenes(self):
        """Real stream-copy cuts retain coupling, seal, then torque frames."""
        if not ffmpeg_available():
            self.skipTest('ffmpeg/ffprobe are not installed')

        fixture = (
            Path(__file__).resolve().parents[2]
            / 'ai/core/evals/golden/fixtures/media/eval-hx200-seal-video.mp4'
        )
        probe = probe_video(str(fixture))
        windows = plan_segments(probe.duration_s, window_s=60, overlap_s=5)
        self.assertEqual(probe.duration_s, 130.0)
        self.assertEqual(windows, [(0.0, 60.0), (55.0, 115.0), (110.0, 130.0)])

        dominant_channels = []
        title_dark_pixels = []
        with tempfile.TemporaryDirectory() as workdir:
            for index, (start, end) in enumerate(windows):
                clip = Path(workdir) / f'clip-{index}.mp4'
                frame = Path(workdir) / f'frame-{index}.jpg'
                cut_segment(str(fixture), start, end - start, str(clip))
                extract_keyframe(str(clip), (end - start) / 2, str(frame))
                with Image.open(frame) as image:
                    rgb = image.convert('RGB')
                    background = ImageStat.Stat(rgb.crop((0, 0, 40, 40))).mean
                    dominant_channels.append(max(range(3), key=background.__getitem__))
                    title_histogram = (
                        rgb.crop((20, 98, 460, 137)).convert('L').histogram()
                    )
                    title_dark_pixels.append(sum(title_histogram[:100]))

        # Blue coupling scene, red seal scene, green torque scene.
        self.assertEqual(dominant_channels, [2, 0, 1])
        # The baked titles have decreasing glyph area in that same order.
        self.assertGreater(title_dark_pixels[0], title_dark_pixels[1])
        self.assertGreater(title_dark_pixels[1], title_dark_pixels[2])


class VideoSegmentReadTests(SimpleTestCase):
    """Inline clip reads are bounded before bytes reach the SDK."""

    def test_segment_read_accepts_content_at_the_limit(self):
        """The exact ceiling is legal and returned byte-for-byte."""
        with tempfile.NamedTemporaryFile() as clip:
            clip.write(b'1234')
            clip.flush()
            self.assertEqual(_read_video_segment_bytes(clip.name, max_bytes=4), b'1234')

    def test_segment_read_rejects_one_byte_over_without_path_leak(self):
        """An oversized inline payload fails under the segment error code."""
        with tempfile.NamedTemporaryFile() as clip:
            clip.write(b'12345')
            clip.flush()
            with self.assertRaises(AttachmentIngestionError) as caught:
                _read_video_segment_bytes(clip.name, max_bytes=4)
        self.assertEqual(caught.exception.code, 'ATTACHMENT_SKIP_VIDEO_OVERSIZE')
        self.assertNotIn(clip.name, str(caught.exception))


class ProbeVideoTests(SimpleTestCase):
    """ffprobe JSON parsing: only value-checked facts leave the module."""

    def _probe(self, payload):
        """Run probe_video against one canned ffprobe JSON payload."""
        with mock.patch(_RUN, return_value=_ffprobe_json(payload)) as run:
            probe = probe_video('/media/clip.mp4')
        return probe, run

    def test_full_probe_parses_duration_stream_and_creation_time(self):
        """A Z-suffixed creation_time parses to an aware UTC datetime."""
        probe, run = self._probe(
            {
                'format': {
                    'duration': '130.500000',
                    'tags': {'creation_time': '2026-03-14T10:30:00.000000Z'},
                },
                'streams': [
                    {'codec_type': 'audio'},
                    {'codec_type': 'video', 'width': 1920, 'height': 1080},
                ],
            }
        )
        self.assertEqual(probe.duration_s, 130.5)
        self.assertEqual(probe.recorded_at, datetime(2026, 3, 14, 10, 30, tzinfo=UTC))
        self.assertIsNotNone(probe.recorded_at.tzinfo)
        self.assertEqual((probe.width, probe.height), (1920, 1080))
        self.assertTrue(probe.has_video_stream)
        argv = run.call_args.args[0]
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[0], 'ffprobe')
        self.assertEqual(argv[-1], '/media/clip.mp4')
        self.assertEqual(run.call_args.kwargs['timeout'], PROBE_TIMEOUT_S)

    def test_missing_creation_time_is_none(self):
        """No container creation_time simply yields None (never a guess)."""
        probe, _run = self._probe(
            {
                'format': {'duration': '12.0'},
                'streams': [{'codec_type': 'video', 'width': 640, 'height': 480}],
            }
        )
        self.assertIsNone(probe.recorded_at)

    def test_garbage_creation_time_is_none(self):
        """Unparsable creation_time collapses to None, not an error."""
        probe, _run = self._probe(
            {
                'format': {
                    'duration': '12.0',
                    'tags': {'creation_time': 'around lunchtime, probably'},
                },
                'streams': [{'codec_type': 'video', 'width': 640, 'height': 480}],
            }
        )
        self.assertIsNone(probe.recorded_at)

    def test_audio_only_container_has_no_video_stream(self):
        """An mp4 with only audio streams reports has_video_stream=False."""
        probe, _run = self._probe(
            {'format': {'duration': '30.0'}, 'streams': [{'codec_type': 'audio'}]}
        )
        self.assertFalse(probe.has_video_stream)
        self.assertIsNone(probe.width)
        self.assertIsNone(probe.height)

    def test_malformed_or_missing_duration_fails_probe_code(self):
        """Every malformed-output shape fails under the probe's own code."""
        payloads = (
            {},
            {'format': {}},
            {'format': {'duration': 'N/A'}},
            {'format': {'duration': '0.0'}},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                with (
                    mock.patch(_RUN, return_value=_ffprobe_json(payload)),
                    self.assertLogs('inventree', level='WARNING'),
                    self.assertRaises(VideoToolError) as caught,
                ):
                    probe_video('/media/clip.mp4')
                self.assertEqual(caught.exception.code, 'ATTACHMENT_VIDEO_PROBE_FAILED')

    def test_non_json_stdout_fails_probe_code(self):
        """Non-JSON ffprobe stdout is a probe failure, not a crash."""
        with (
            mock.patch(_RUN, return_value=_completed(stdout=b'not json at all')),
            self.assertLogs('inventree', level='WARNING'),
            self.assertRaises(VideoToolError) as caught,
        ):
            probe_video('/media/clip.mp4')
        self.assertEqual(caught.exception.code, 'ATTACHMENT_VIDEO_PROBE_FAILED')


class ArgvPinTests(SimpleTestCase):
    """The exact tool invocations: list argv, seek-before-input, stream copy."""

    def test_cut_segment_argv_stream_copies_with_pre_input_seek(self):
        """-ss precedes -i (fast seek) and the clip is -c copy, never re-encoded."""
        with mock.patch(_RUN, return_value=_completed()) as run:
            cut_segment('/media/src.mov', 55.0, 60.0, '/tmp/clip-1.mov')
        argv = run.call_args.args[0]
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[0], 'ffmpeg')
        self.assertLess(argv.index('-ss'), argv.index('-i'))
        self.assertEqual(argv[argv.index('-ss') + 1], '55.000')
        self.assertEqual(argv[argv.index('-i') + 1], '/media/src.mov')
        self.assertEqual(argv[argv.index('-t') + 1], '60.000')
        map_positions = [index for index, value in enumerate(argv) if value == '-map']
        self.assertEqual([argv[index + 1] for index in map_positions], ['0:v:0', '0:a:0?'])
        self.assertIn('-dn', argv)
        self.assertIn('-sn', argv)
        self.assertEqual(argv[argv.index('-c') + 1], 'copy')
        self.assertEqual(argv[-1], '/tmp/clip-1.mov')
        self.assertEqual(run.call_args.kwargs['timeout'], CUT_TIMEOUT_S)

    def test_extract_keyframe_argv_grabs_exactly_one_frame(self):
        """The keyframe grab seeks before input and asks for one frame."""
        with mock.patch(_RUN, return_value=_completed()) as run:
            extract_keyframe('/tmp/clip-1.mov', 30.0, '/tmp/frame-1.jpg')
        argv = run.call_args.args[0]
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[0], 'ffmpeg')
        self.assertLess(argv.index('-ss'), argv.index('-i'))
        self.assertEqual(argv[argv.index('-ss') + 1], '30.000')
        self.assertEqual(argv[argv.index('-frames:v') + 1], '1')
        self.assertEqual(argv[-1], '/tmp/frame-1.jpg')
        self.assertEqual(run.call_args.kwargs['timeout'], KEYFRAME_TIMEOUT_S)


class ToolFailureTests(SimpleTestCase):
    """Failure legs: per-tool codes, and stderr NEVER leaves the subprocess."""

    def _cases(self):
        """Each tool call paired with its failure code."""
        return (
            (lambda: probe_video('/in.mp4'), 'ATTACHMENT_VIDEO_PROBE_FAILED'),
            (
                lambda: cut_segment('/in.mp4', 0.0, 60.0, '/out.mp4'),
                'ATTACHMENT_VIDEO_SEGMENT_FAILED',
            ),
            (
                lambda: extract_keyframe('/in.mp4', 30.0, '/out.jpg'),
                'ATTACHMENT_VIDEO_KEYFRAME_FAILED',
            ),
        )

    def test_nonzero_rc_raises_per_tool_code_without_leaking_stderr(self):
        """A nonzero rc fails under the tool's code and never surfaces stderr.

        The sentinel must appear in neither str(exc) nor exc.code nor any
        log record.
        """
        failing = _completed(
            returncode=1, stdout=b'SECRET_STDERR', stderr=b'SECRET_STDERR'
        )
        for call, code in self._cases():
            with self.subTest(code=code):
                with (
                    mock.patch(_RUN, return_value=failing),
                    self.assertLogs('inventree', level='WARNING') as captured,
                    self.assertRaises(VideoToolError) as caught,
                ):
                    call()
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn('SECRET_STDERR', str(caught.exception))
                self.assertNotIn('SECRET_STDERR', caught.exception.code)
                self.assertNotIn('SECRET_STDERR', '\n'.join(captured.output))

    def test_timeout_raises_per_tool_code_without_leaking_stderr(self):
        """A hung tool fails under the same code; captured output stays inside."""
        timeout = subprocess.TimeoutExpired(
            cmd=['ffmpeg'], timeout=1, output=b'SECRET_STDERR', stderr=b'SECRET_STDERR'
        )
        for call, code in self._cases():
            with self.subTest(code=code):
                with (
                    mock.patch(_RUN, side_effect=timeout),
                    self.assertLogs('inventree', level='WARNING') as captured,
                    self.assertRaises(VideoToolError) as caught,
                ):
                    call()
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn('SECRET_STDERR', str(caught.exception))
                self.assertNotIn('SECRET_STDERR', '\n'.join(captured.output))

    def test_missing_binary_raises_unavailable(self):
        """FileNotFoundError means the image lacks ffmpeg: a distinct code."""
        for call, _code in self._cases():
            with self.subTest(call=call):
                with (
                    mock.patch(_RUN, side_effect=FileNotFoundError('ffmpeg')),
                    self.assertRaises(VideoToolError) as caught,
                ):
                    call()
                self.assertEqual(
                    caught.exception.code, 'ATTACHMENT_VIDEO_TOOL_UNAVAILABLE'
                )


class FfmpegAvailableTests(SimpleTestCase):
    """The boot probe requires BOTH binaries on PATH."""

    def test_requires_both_binaries(self):
        """True only when ffmpeg AND ffprobe resolve."""
        with mock.patch(_WHICH, side_effect=lambda name: f'/usr/bin/{name}'):
            self.assertTrue(ffmpeg_available())
        for missing in ('ffmpeg', 'ffprobe'):
            with (
                self.subTest(missing=missing),
                mock.patch(
                    _WHICH,
                    side_effect=lambda name, gone=missing: (
                        None if name == gone else f'/usr/bin/{name}'
                    ),
                ),
            ):
                self.assertFalse(ffmpeg_available())
        with mock.patch(_WHICH, return_value=None):
            self.assertFalse(ffmpeg_available())
