"""Fork-owned attachment RAG signal receivers (R1).

Registered from ``AIChatConfig.ready()`` so the upstream-owned ``common`` app
is never edited. Every handler is fail-soft: ingestion must never break an
upload, a delete, or a linkage change. Heavy work always leaves the request
path via ``offload_task(..., force_async=True, group='ai-ingest')``.
"""

import logging

from django.conf import settings as django_settings

logger = logging.getLogger('inventree')

_INGEST_GROUP = 'ai-ingest'


def _log_receiver_fault(event: str, exc: BaseException) -> None:
    """Value-free fault record (F-13): never the exception text, only where.

    A pydantic ValidationError here could carry configured endpoint values;
    ``log_fault`` records error type + raise location only.
    """
    try:
        from ai.core.faults import log_fault

        log_fault(
            logger, event, exc, stage='attachment_receiver', level=logging.WARNING
        )
    except Exception:
        logger.warning('%s (fault detail unavailable)', event)


def _effective_ai_flags() -> tuple[bool, bool, bool, bool]:
    """The four POST-DEGRADE AI-plane flags; fail-LOUD all-True on error.

    R5 posture C: the Django pair defaults on but has no degrade concept
    (settings.py cannot consult the AI plane at boot), so every Django-side
    gate must AND the effective AI value — otherwise a provider-less fork
    writes a registry row plus a metadata stamp on EVERY upload.

    Exception semantics are deliberately the OPPOSITE of a degrade: a
    DEGRADED plane (constructible settings, missing providers) reads dark
    and stays quiet, but a BROKEN config (constructor raises — explicit
    flag without providers, malformed values) reads all-True so the offload
    still fires and the task fails LOUDLY (F-15: a misconfigured-but-enabled
    deployment must never become silent never-ingestion).
    """
    try:
        from ai.core.config import get_settings

        settings = get_settings()
        return (
            bool(settings.feature_attachment_rag_ingest),
            bool(settings.feature_attachment_rag_retrieval),
            bool(settings.feature_media_rag_ingest),
            bool(settings.feature_media_rag_retrieval),
        )
    except Exception:
        return (True, True, True, True)


def _any_ingest_effective() -> bool:
    """True when ANY ingest pipeline can actually run (post-degrade).

    Mirrors the router: the doc arm needs the effective attachment-ingest
    flag; the media arms need the Django media co-gate AND the effective
    media-ingest flag. The receiver is the SOLE live enqueue point for BOTH,
    so gating on the attachment bit alone would silence all media ingest on
    an attachment-dark/media-lit deployment (R5 review finding, HIGH).
    """
    flags = _effective_ai_flags()
    if flags[0]:
        return True
    return bool(getattr(django_settings, 'AIMMS_MEDIA_RAG_ENABLED', False)) and flags[2]


def _rag_enabled() -> bool:
    """Django-plane master gate ANDed with any effective ingest arm (R5).

    Unbridged ⇒ structurally off; a degraded AI plane reads as off, so
    default-on can never enqueue ingests a deployment cannot run — while a
    RAISING config still offloads loudly (see _effective_ai_flags).
    """
    if not bool(getattr(django_settings, 'AIMMS_ATTACHMENT_RAG_ENABLED', False)):
        return False
    return _any_ingest_effective()


def _restamp_enabled() -> bool:
    """Gate for the scope re-stamp receivers: attachment OR media plane.

    Re-stamps must keep flowing as long as EITHER corpus can serve — a
    deployment that turns the doc plane off while media retrieval stays lit
    would otherwise stop propagating client/coordinate changes into content
    it is still serving (review finding, R3). R5: restamps write to Search,
    so at least one EFFECTIVE AI-plane flag must be lit too — a provider-less
    fork must not enqueue restamp tasks that can only fail.
    """
    django_lit = bool(
        getattr(django_settings, 'AIMMS_ATTACHMENT_RAG_ENABLED', False)
    ) or bool(getattr(django_settings, 'AIMMS_MEDIA_RAG_ENABLED', False))
    if not django_lit:
        return False
    return any(_effective_ai_flags())


def _flag_dependent_skip_matches(reason: str) -> bool:
    """Whether a flag-dependent skip stamp still suppresses offloads.

    Revival semantics (F-08/F-10/C4): a ``PIPELINE_DISABLED`` skip stops
    matching the moment the ingest flag turns on; the media clause reads
    ``media_ingest_enabled`` — the EXACT predicate the router enforces (R3),
    so a flag-pair flip revives each stamp once and a partial flip revives
    nothing; the video clause stays inert until the R4 router change
    (``VIDEO_ROUTER_HONORS_FLAG``). A broken AI config must return False —
    the offloaded task then fails loudly, instead of the stamp silently
    re-creating the F-15 swallow.
    """
    from aichat.services.attachment_ingestion import (
        VIDEO_ROUTER_HONORS_FLAG,
        media_ingest_enabled,
    )

    try:
        from ai.core.config import get_settings

        settings = get_settings()
    except Exception:
        return False
    if reason == 'ATTACHMENT_SKIP_PIPELINE_DISABLED':
        return not settings.feature_attachment_rag_ingest
    if reason == 'ATTACHMENT_SKIP_MEDIA_PIPELINE_DARK':
        return not media_ingest_enabled(settings)
    if reason == 'ATTACHMENT_SKIP_VIDEO_PIPELINE_DARK':
        if not VIDEO_ROUTER_HONORS_FLAG:
            return True
        return not media_ingest_enabled(settings)
    return True


def _stamp_matches(instance) -> bool:
    """True when the metadata stamp already covers this exact file revision.

    Defeats the documented double-save (file-size backfill + thumbnail
    rebuild) and metadata-only edits without hashing on the request path.
    Failed ingests deliberately do not match, so a later save retries.

    v2 contract (F-02/F-03): versioned — a router/sniff change bumps the
    version and revives everything once; flag-dependent skips revive on flag
    flip; and the storage mtime (statted last, only when everything else
    matched) catches in-place content replacement at identical name+size.
    """
    from aichat.services.attachment_ingestion import (
        FLAG_DEPENDENT_SKIPS,
        STAMP_VERSION,
        storage_mtime,
    )

    stamp = (instance.metadata or {}).get('ai_ingest') or {}
    if stamp.get('v') != STAMP_VERSION:
        return False
    if stamp.get('state') not in ('indexed', 'skipped'):
        return False
    if stamp.get('name') != (instance.attachment.name or ''):
        return False
    if stamp.get('size') != (instance.file_size or 0):
        return False
    reason = str(stamp.get('reason') or '')
    if (
        stamp.get('state') == 'skipped'
        and reason in FLAG_DEPENDENT_SKIPS
        and not _flag_dependent_skip_matches(reason)
    ):
        return False
    stamped_mtime = stamp.get('mtime')
    if stamped_mtime:
        current_mtime = storage_mtime(instance)
        if current_mtime is not None and current_mtime != stamped_mtime:
            return False
    return True


def attachment_saved(sender, instance, created, **kwargs):
    """Offload ingestion for allow-listed, structurally acceptable uploads."""
    try:
        if not _rag_enabled():
            return
        from aichat.services.attachment_ingestion import (
            RECEIVER_MODEL_TYPES,
            structural_skip_reason,
        )

        # Doc owners get ingested; WO/step/repairpacket owners get their
        # decision-#10 recorded skips (review finding F-07).
        if instance.model_type not in RECEIVER_MODEL_TYPES:
            return
        if structural_skip_reason(instance) is not None:
            return
        if _stamp_matches(instance):
            return
        from aichat import tasks as aichat_tasks
        from InvenTree.tasks import offload_task

        offload_task(
            aichat_tasks.ingest_attachment,
            instance.pk,
            force_async=True,
            group=_INGEST_GROUP,
        )
    except Exception as exc:
        _log_receiver_fault('Attachment ingest scheduling failed (ignored)', exc)


def attachment_deleted(sender, instance, **kwargs):
    """Offload index/registry purge whenever ingested artifacts exist.

    Deliberately not flag-gated: content deleted while the flag is off must
    still leave the serving index (denial ≡ nonexistence).
    """
    try:
        from aichat.models import AttachmentIngest

        if not AttachmentIngest.objects.filter(attachment_id=instance.pk).exists():
            return
        from aichat import tasks as aichat_tasks
        from InvenTree.tasks import offload_task

        offload_task(
            aichat_tasks.purge_attachment,
            instance.pk,
            force_async=True,
            group=_INGEST_GROUP,
        )
    except Exception as exc:
        _log_receiver_fault('Attachment purge scheduling failed (ignored)', exc)


def machine_part_changed(sender, instance, **kwargs):
    """Re-stamp a part's derived ``client_codes`` when its linkage changes."""
    try:
        if not _rag_enabled():
            return
        from aichat.models import AttachmentIngest

        if not AttachmentIngest.objects.filter(
            model_type='part', model_id=instance.part_id
        ).exists():
            return
        from aichat import tasks as aichat_tasks
        from InvenTree.tasks import offload_task

        offload_task(
            aichat_tasks.restamp_part_client_codes,
            instance.part_id,
            force_async=True,
            group=_INGEST_GROUP,
        )
    except Exception as exc:
        _log_receiver_fault('Client-code re-stamp scheduling failed (ignored)', exc)


def asset_machine_saved(sender, instance, **kwargs):
    """Re-stamp machine and installed-part docs after a possible client change."""
    try:
        if not _restamp_enabled():
            return
        from aichat.models import AttachmentIngest
        from assets.models import MachinePart

        has_docs = AttachmentIngest.objects.filter(
            model_type='assetmachine', model_id=instance.pk
        ).exists()
        has_linked_parts = MachinePart.objects.filter(machine_id=instance.pk).exists()
        has_wo_media = False
        if not has_docs and not has_linked_parts:
            # A machine client change also reaches WO/step evidence (R3).
            try:
                from django.db.models import Q

                from tasks.models import WorkOrder
                from tasks.procedure_models import WorkOrderStepExecution

                work_order_ids = list(
                    WorkOrder.objects.filter(machine_id=instance.pk).values_list(
                        'pk', flat=True
                    )
                )
                step_ids = WorkOrderStepExecution.objects.filter(
                    application__work_order_id__in=work_order_ids
                ).values_list('pk', flat=True)
                has_wo_media = AttachmentIngest.objects.filter(
                    Q(model_type='workorder', model_id__in=work_order_ids)
                    | Q(model_type='workorderstepexecution', model_id__in=step_ids)
                ).exists()
            except Exception:
                has_wo_media = False
        if not has_docs and not has_linked_parts and not has_wo_media:
            return
        from aichat import tasks as aichat_tasks
        from InvenTree.tasks import offload_task

        offload_task(
            aichat_tasks.restamp_machine_client_codes,
            instance.pk,
            force_async=True,
            group=_INGEST_GROUP,
        )
    except Exception as exc:
        _log_receiver_fault('Client-code re-stamp scheduling failed (ignored)', exc)


def work_order_saved(sender, instance, **kwargs):
    """Re-stamp a work order's evidence media after machine/customer changes.

    Existence-gated like the machine receiver: no SearchClients, no offloads,
    unless indexed media rows for this WO or its step executions exist.
    """
    try:
        if not _restamp_enabled():
            return
        from tasks.procedure_models import WorkOrderStepExecution

        from aichat.models import AttachmentIngest

        step_ids = WorkOrderStepExecution.objects.filter(
            application__work_order_id=instance.pk
        ).values_list('pk', flat=True)
        from django.db.models import Q

        has_media = AttachmentIngest.objects.filter(
            Q(model_type='workorder', model_id=instance.pk)
            | Q(model_type='workorderstepexecution', model_id__in=step_ids)
        ).exists()
        if not has_media:
            return
        from aichat import tasks as aichat_tasks
        from InvenTree.tasks import offload_task

        offload_task(
            aichat_tasks.restamp_work_order_media,
            instance.pk,
            force_async=True,
            group=_INGEST_GROUP,
        )
    except Exception as exc:
        _log_receiver_fault('Client-code re-stamp scheduling failed (ignored)', exc)
