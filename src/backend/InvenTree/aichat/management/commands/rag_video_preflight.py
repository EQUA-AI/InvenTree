"""RUN 4 video-pipeline preflight: audit-only by default, writes behind flags.

Audits everything the video path depends on (binaries, flags, caps, storage
writability) and, behind ``--set-upload-max-mb``, raises the runtime upload
cap through the sanctioned idempotent path (``set_global_setting``). One run
per environment is a RUN 4 rollout precondition.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Audit (and optionally provision) the video-ingest preconditions."""

    help = (
        'Audit ffmpeg/flags/caps/storage for the video RAG pipeline; '
        'optionally raise INVENTREE_UPLOAD_MAX_SIZE with --set-upload-max-mb.'
    )

    def add_arguments(self, parser):
        """Register the explicit write flag (audit-only without it)."""
        parser.add_argument(
            '--set-upload-max-mb',
            type=int,
            default=0,
            help='Set the runtime INVENTREE_UPLOAD_MAX_SIZE (MB); 0 = audit only',
        )

    def handle(self, *args, **options):
        """Print the audit table; apply the cap only when asked."""
        import subprocess

        from django.conf import settings as django_settings

        from aichat.services.video_tools import ffmpeg_available
        from common.settings import get_global_setting, set_global_setting

        available = ffmpeg_available()
        version = ''
        if available:
            try:
                completed = subprocess.run(
                    ['ffprobe', '-version'],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                version = (
                    (completed.stdout or b'').decode(errors='replace').splitlines()[0]
                )
            except Exception:
                version = 'unknown'
        self.stdout.write(
            f'ffmpeg/ffprobe: {"OK " + version if available else "MISSING"}'
        )

        for flag in ('AIMMS_ATTACHMENT_RAG_ENABLED', 'AIMMS_MEDIA_RAG_ENABLED'):
            self.stdout.write(f'{flag} = {getattr(django_settings, flag, False)}')
        try:
            from ai.core.config import get_settings

            ai_settings = get_settings()
            self.stdout.write(
                'FEATURE_MEDIA_RAG_INGEST = '
                f'{ai_settings.feature_media_rag_ingest}; '
                f'segment={ai_settings.rag_video_segment_s}s '
                f'overlap={ai_settings.rag_video_overlap_s}s '
                f'max_duration={ai_settings.rag_video_max_duration_s}s '
                f'max_size={ai_settings.rag_max_video_mb}MB'
            )
        except Exception as exc:
            self.stdout.write(f'AI config unavailable ({type(exc).__name__})')

        current = get_global_setting('INVENTREE_UPLOAD_MAX_SIZE', create=False)
        self.stdout.write(f'INVENTREE_UPLOAD_MAX_SIZE = {current} MB')
        target = options['set_upload_max_mb']
        if target:
            set_global_setting('INVENTREE_UPLOAD_MAX_SIZE', target, None)
            self.stdout.write(
                self.style.SUCCESS(f'INVENTREE_UPLOAD_MAX_SIZE set to {target} MB')
            )

        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        probe_path = 'ai/keyframes/.preflight'
        try:
            if default_storage.exists(probe_path):
                default_storage.delete(probe_path)
            default_storage.save(probe_path, ContentFile(b'ok'))
            default_storage.delete(probe_path)
            self.stdout.write('keyframes dir: writable')
        except Exception as exc:
            self.stdout.write(f'keyframes dir: NOT writable ({type(exc).__name__})')
