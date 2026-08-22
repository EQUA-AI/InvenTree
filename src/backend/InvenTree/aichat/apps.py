"""Application configuration for durable AI chat persistence."""

import logging

from django.apps import AppConfig

logger = logging.getLogger('inventree')


class AIChatConfig(AppConfig):
    """Configure the durable AI chat application."""

    default_auto_field = 'django.db.models.AutoField'
    name = 'aichat'
    verbose_name = 'AI Chat'

    def ready(self):
        """Keep the AI capability selector's category lexicon fresh.

        Category names feed tool selection, so a newly created category must be
        recognizable on the next turn rather than after its cache entry expires.
        Registration is best-effort: the lexicon already fails open, and chat must
        not block application startup.
        """
        try:
            from django.db.models.signals import post_delete, post_save

            from ai.core.tools.capabilities import invalidate_category_lexicon
            from part.models import PartCategory

            post_save.connect(
                invalidate_category_lexicon,
                sender=PartCategory,
                dispatch_uid='aichat.capability_category_lexicon',
            )
            post_delete.connect(
                invalidate_category_lexicon,
                sender=PartCategory,
                dispatch_uid='aichat.capability_category_lexicon',
            )
        except (
            ImportError,
            RuntimeError,
        ) as exc:  # pragma: no cover - part app absent in AI-only settings
            # Expected in AI-only deployments/tests where the part app is not
            # installed; the lexicon fails open, so this is informational.
            logger.info(
                'Category lexicon invalidation not registered',
                extra={'error_type': type(exc).__name__},
            )
        except Exception:  # pragma: no cover - startup must not depend on AI config
            logger.warning(
                'Could not register category lexicon invalidation', exc_info=True
            )

        self._register_attachment_rag_receivers()
        self._probe_attachment_rag_config()

    def _probe_attachment_rag_config(self):
        """Fail loudly at boot when RAG is enabled but its AI config is broken.

        The receivers swallow per-upload errors by design (uploads must never
        break), which previously turned a misconfigured-but-enabled deployment
        into silent never-ingestion (review finding F-15). The validators are
        value-free-logged: a pydantic error can carry configured input values,
        so only the failing field locations are recorded.
        """
        from django.conf import settings as django_settings

        if not (
            getattr(django_settings, 'AIMMS_ATTACHMENT_RAG_ENABLED', False)
            or getattr(django_settings, 'AIMMS_MEDIA_RAG_ENABLED', False)
        ):
            return
        try:
            from ai.core.config import get_settings

            get_settings()
            if getattr(django_settings, 'AIMMS_MEDIA_RAG_ENABLED', False):
                from ai.core.integrations.doc_intelligence import (
                    get_doc_intelligence_client,
                )

                if get_doc_intelligence_client() is None:
                    logger.error(
                        'Media RAG is enabled but Document Intelligence is not '
                        'configured on THIS process; image ingests executed '
                        'here will fail. Harmless on a web app whose worker '
                        'carries AZURE_DOC_INTELLIGENCE_ENDPOINT/_KEY.'
                    )
                from aichat.services.video_tools import ffmpeg_available

                if not ffmpeg_available():
                    logger.error(
                        'Media RAG is enabled but ffmpeg/ffprobe is not on '
                        'PATH in THIS image; video ingests will fail. Every '
                        'app sharing this image is affected.'
                    )
        except Exception as exc:
            locations: list[str] = []
            errors = getattr(exc, 'errors', None)
            if callable(errors):
                try:
                    locations = [
                        '.'.join(str(part) for part in error.get('loc', ()))
                        for error in errors()
                    ]
                except Exception:
                    locations = []
            logger.error(
                'Attachment RAG is enabled but its AI configuration is invalid; '
                'ingestion will NOT run (error_type=%s fields=%s)',
                type(exc).__name__,
                ','.join(locations) or 'unknown',
            )

    def _register_attachment_rag_receivers(self):
        """Wire the R1 attachment-RAG receivers onto upstream/fork senders.

        Fork-owned receivers keep the upstream-sync surface at zero (the
        ``common`` app is never edited). Handlers gate themselves on the
        bridged ``AIMMS_ATTACHMENT_RAG_ENABLED`` flag, so registration is
        unconditional and harmless while dark.
        """
        try:
            from django.db.models.signals import post_delete, post_save

            from aichat import receivers
            from common.models import Attachment

            post_save.connect(
                receivers.attachment_saved,
                sender=Attachment,
                dispatch_uid='aichat.attachment_rag_saved',
            )
            post_delete.connect(
                receivers.attachment_deleted,
                sender=Attachment,
                dispatch_uid='aichat.attachment_rag_deleted',
            )
        except (ImportError, RuntimeError) as exc:
            # Expected in AI-only settings where the common app is absent.
            logger.info(
                'Attachment RAG receivers not registered',
                extra={'error_type': type(exc).__name__},
            )
        except Exception:  # pragma: no cover - startup must not depend on AI config
            logger.warning('Could not register attachment RAG receivers', exc_info=True)

        try:
            from django.db.models.signals import post_delete, post_save

            from aichat import receivers
            from assets.models import AssetMachine, MachinePart

            post_save.connect(
                receivers.machine_part_changed,
                sender=MachinePart,
                dispatch_uid='aichat.attachment_rag_machinepart_saved',
            )
            post_delete.connect(
                receivers.machine_part_changed,
                sender=MachinePart,
                dispatch_uid='aichat.attachment_rag_machinepart_deleted',
            )
            post_save.connect(
                receivers.asset_machine_saved,
                sender=AssetMachine,
                dispatch_uid='aichat.attachment_rag_machine_saved',
            )
        except (ImportError, RuntimeError) as exc:
            # Expected in AI-only settings where the assets app is absent.
            logger.info(
                'Attachment RAG re-stamp receivers not registered',
                extra={'error_type': type(exc).__name__},
            )
        except Exception:  # pragma: no cover - startup must not depend on AI config
            logger.warning(
                'Could not register attachment RAG re-stamp receivers', exc_info=True
            )

        try:
            from django.db.models.signals import post_save

            from tasks.models import WorkOrder

            from aichat import receivers

            post_save.connect(
                receivers.work_order_saved,
                sender=WorkOrder,
                dispatch_uid='aichat.attachment_rag_workorder_saved',
            )
        except (ImportError, RuntimeError) as exc:
            # Expected in AI-only settings where the tasks app is absent.
            logger.info(
                'Attachment RAG work-order re-stamp receiver not registered',
                extra={'error_type': type(exc).__name__},
            )
        except Exception:  # pragma: no cover - startup must not depend on AI config
            logger.warning(
                'Could not register attachment RAG work-order receiver', exc_info=True
            )
