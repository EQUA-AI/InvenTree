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
        except (ImportError, RuntimeError) as exc:  # pragma: no cover - part app absent in AI-only settings
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
