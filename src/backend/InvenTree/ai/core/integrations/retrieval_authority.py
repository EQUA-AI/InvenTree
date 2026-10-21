"""Final native reauthorization before Search excerpts cross into model context.

Search filters remain mandatory candidate selectors. A projection, cached actor
or earlier successful scope resolution is never the final authority.
"""


def fresh_actor(user):
    """Discard cached permission/scope attributes and reject erased accounts."""
    from django.contrib.auth import get_user_model

    pk = getattr(user, "pk", None)
    if type(pk) is not int or pk < 1:
        return None
    return get_user_model().objects.filter(pk=pk, is_active=True).first()


def fresh_role(actor, role):
    """Read the role relation directly; session permission caches may be stale."""
    from users.models import RuleSet

    return bool(
        actor.is_superuser
        or RuleSet.objects.filter(group__user=actor, name=role, can_view=True).exists()
    )


def attachment_rows(rows, *, user, corpus):
    """Recheck current source, ingest, chunk, role and client coordinates."""
    from ai.core.config import get_settings
    from ai.core.integrations.projection_gate import recall_allowed
    from aichat.services.projection_authority import projection_row_reason
    from tasks.scope import ScopeError, client_codes_for_actor

    if corpus not in {"attachment", "media"} or len(rows) > 15:
        return []
    actor = fresh_actor(user)
    if actor is None or not fresh_role(actor, "work_order") or not recall_allowed(corpus):
        return []
    settings = get_settings()
    if not getattr(settings, f"feature_{corpus}_rag_retrieval", False):
        return []
    try:
        clients = client_codes_for_actor(actor)
    except ScopeError:
        # A client grant remains mandatory for machine/part evidence. Shared
        # Work evidence is authorized from its current native owner below.
        clients = frozenset()
    if corpus == "attachment":
        arms = ("part", "assetmachine") if fresh_role(actor, "part") else ("assetmachine",)
        index_name = settings.azure_search_attachment_docs_index
    else:
        arms = ("workorder", "workorderstepexecution", "assetmachine")
        index_name = settings.azure_search_media_index
    accepted = []
    for row in rows:
        # The shared projection policy compares the complete projected ACL against current native
        # ownership, not merely the intersection that made Search return it.
        if row.get("model_type") not in arms or projection_row_reason(
            row, corpus=corpus, index_name=index_name
        ):
            continue
        if row.get("model_type") in {"workorder", "workorderstepexecution"}:
            from common.models import Attachment

            attachment = Attachment.objects.filter(pk=row.get("attachment_id")).first()
            if attachment is None or not native_attachment_owner(actor, attachment):
                continue
        elif not set(row.get("client_codes") or ()).intersection(clients):
            continue
        accepted.append(row)
    return accepted


def controlled_rows(rows, *, user):
    """Site-wide controlled manuals retain their existing work-order reader role."""
    from ai.core.config import get_settings
    from ai.core.integrations.projection_gate import recall_allowed
    from aichat.models import ControlledDocument

    actor = fresh_actor(user)
    from InvenTree.restore_hold import restore_hold_enabled

    if (
        actor is None
        or not fresh_role(actor, "work_order")
        or len(rows) > 5
        or restore_hold_enabled()
        or not recall_allowed("controlled")
    ):
        return []
    settings = get_settings()
    if not settings.single_site_policy_key:
        return []
    accepted = []
    for row in rows:
        if (
            row.get("scope_key") != settings.single_site_policy_key
            or row.get("is_current") is not True
        ):
            continue
        document = ControlledDocument.objects.filter(
            scope_key=settings.single_site_policy_key,
            document_id=row.get("document_id"),
            revision=row.get("document_revision"),
            source_sha256=row.get("source_sha256"),
            search_index_name=settings.azure_search_controlled_documents_index,
            is_current=True,
            state="indexed",
            access_class="maintenance_authorized",
        ).first()
        if (
            document is None
            or row.get("access_class") != document.access_class
            or str(row.get("asset_id") or "") != document.asset_id
        ):
            continue
        accepted.append(row)
    return accepted


def native_attachment_owner(actor, attachment):
    """Authorize inventory metadata even when no ingest/ACL stamp exists."""
    from tasks.scope import ScopeError, require_machine_scope, require_work_order_scope

    try:
        if attachment.model_type == "assetmachine":
            from assets.models import AssetMachine

            machine = AssetMachine.objects.filter(pk=attachment.model_id, active=True).first()
            if machine is None:
                return False
            require_machine_scope(actor, machine)
            return True
        if attachment.model_type in {"workorder", "workorderstepexecution"}:
            from tasks.models import WorkOrder

            if attachment.model_type == "workorder":
                work_order = (
                    WorkOrder.objects
                    .select_related("machine")
                    .filter(pk=attachment.model_id)
                    .first()
                )
            else:
                from tasks.procedure_models import WorkOrderStepExecution

                execution = (
                    WorkOrderStepExecution.objects
                    .select_related("application__work_order__machine")
                    .filter(pk=attachment.model_id)
                    .first()
                )
                work_order = execution.application.work_order if execution else None
            if work_order is None:
                return False
            require_work_order_scope(actor, work_order)
            return True
    except ScopeError:
        return False
    return False


def _exact_part_owner_authorized(actor, attachment) -> bool:
    """Deliberate Part extension of the native owner policy (AT-R02).

    ``native_attachment_owner`` covers machines and work orders only, so the
    exact-target path reuses the *policy* for Parts instead of assuming that
    function supports them: real Part existence plus the existing
    ``derive_client_codes`` intersection (installed-machine client codes,
    ``internal`` only for genuinely unlinked parts, linked-but-clientless
    denied -- fail-closed, never a permissive shortcut).
    """
    from tasks.scope import ScopeError, client_codes_for_actor

    try:
        from part.models import Part

        if not Part.objects.filter(pk=attachment.model_id).exists():
            return False
        from aichat.services.attachment_ingestion import derive_client_codes

        codes = derive_client_codes("part", attachment.model_id)
        if not codes:
            return False
        return bool(set(codes).intersection(client_codes_for_actor(actor)))
    except ScopeError:
        return False


def _exact_owner_authorized(actor, attachment) -> bool:
    """Native owner authorization for one exact-target attachment."""
    if attachment.model_type == "part":
        return _exact_part_owner_authorized(actor, attachment)
    return native_attachment_owner(actor, attachment)


def row_matches_exact_target(row, target) -> bool:
    """Pure equality between one returned row and a bound exact target.

    Search-filter acceptance grants nothing: attachment, native owner and
    source hash must all match the bound target or the row is rejected.
    """
    if not target or not isinstance(row, dict):
        return False
    return (
        row.get("attachment_id") == target.get("attachment_id")
        and row.get("model_type") == target.get("model_type")
        and row.get("model_id") == target.get("model_id")
        and row.get("source_sha256") == target.get("source_sha256")
    )


def _compatible_profiles(settings):
    """Embedding profiles acceptable for the ACTIVE retrieval settings.

    Mirrors the corpus row filter exactly: baseline retrieval accepts the
    baseline stamp (or an empty stamp); any other active profile accepts
    only itself. A stale ingest profile is unavailable before provider work;
    the projected row never rehabilitates it (AT-F1 R07/R08).
    """
    from ai.core.integrations.rag_profile import BASELINE_PROFILE, text_embedding_profile

    active = text_embedding_profile(settings)
    return (BASELINE_PROFILE, None, "") if active == BASELINE_PROFILE else (active,)


def exact_attachment_target(user, attachment_id):
    """Bind one EXACT uploaded attachment or refuse (fail-closed, AT-F1).

    Returns the bound coordinates (attachment id, native ``model_type``/
    ``model_id``, owner serial, active latest serving-index ingest hash) or
    ``None``. Missing, deleted, unsupported-owner, unindexed, superseded,
    wrong-index, role-less and unauthorized targets are indistinguishable:
    callers must refuse with one typed value-free code and never enumerate.
    """
    from ai.core.config import get_settings
    from aichat.models import AttachmentIngest
    from common.models import Attachment
    from django.db.models import F

    if type(attachment_id) is not int or attachment_id < 1:
        return None
    actor = fresh_actor(user)
    # Every reader needs the work-order role; part-owned documents
    # additionally need the part role (the corpus arms' own RBAC).
    if actor is None or not fresh_role(actor, "work_order"):
        return None
    settings = get_settings()
    if not getattr(settings, "feature_attachment_rag_retrieval", False):
        return None
    attachment = Attachment.objects.filter(pk=attachment_id).first()
    if attachment is None or attachment.model_type not in {"assetmachine", "part"}:
        return None
    if attachment.model_type == "part" and not fresh_role(actor, "part"):
        return None
    if not _exact_owner_authorized(actor, attachment):
        return None
    owner_serial = ""
    if attachment.model_type == "assetmachine":
        from assets.models import AssetMachine

        machine = AssetMachine.objects.filter(pk=attachment.model_id, active=True).first()
        if machine is None:
            return None
        owner_serial = machine.serial or ""
    ingest_rows = AttachmentIngest.objects.filter(
        attachment_id=attachment_id,
        search_index_name=settings.azure_search_attachment_docs_index,
    )
    ingest = ingest_rows.order_by(F("claimed_at").desc(nulls_last=True), "-pk").first()
    if ingest is None or ingest.state != "indexed" or ingest.pipeline != "doc":
        return None
    # AT-F1 R07/R08: the stored ingest must belong to the CURRENT native
    # owner and to the ACTIVE embedding profile; a stale row is unavailable
    # before any provider work, indistinguishable from any other refusal.
    if ingest.model_type != attachment.model_type or ingest.model_id != attachment.model_id:
        return None
    if ingest.embedding_profile not in _compatible_profiles(settings):
        return None
    return {
        "attachment_id": attachment.pk,
        "model_type": attachment.model_type,
        "model_id": attachment.model_id,
        "owner_serial": owner_serial,
        "source_sha256": ingest.source_sha256,
        "embedding_profile": ingest.embedding_profile,
        "search_index_name": ingest.search_index_name,
        "ingest_state": ingest.state,
        "pipeline": ingest.pipeline,
    }


_EXACT_BINDING_FIELDS = (
    "attachment_id",
    "model_type",
    "model_id",
    "owner_serial",
    "source_sha256",
    "embedding_profile",
    "search_index_name",
    "ingest_state",
    "pipeline",
)


def exact_target_binding_current(user, target) -> bool:
    """True only while a FRESH full re-derivation still equals the binding.

    Re-runs the complete native policy (fresh actor/role/grant, owner
    authorization, ingest owner coherence, active profile, index and state)
    and compares every bound field, so a mid-provider revocation, ownership
    change or source change is caught at the same typed, value-free boundary.
    """
    if not target:
        return False
    current = exact_attachment_target(user, target.get("attachment_id"))
    if not current:
        return False
    return all(current.get(field) == target.get(field) for field in _EXACT_BINDING_FIELDS)


def exact_target_row_reason(row, *, target, user) -> str:
    """Exposure-time check for one returned row against a bound target.

    Empty string accepts; any other value rejects. Re-runs the native
    authorization against FRESH actor/role/grant/ownership state AND requires
    the CURRENT native owner and full source binding to still equal the bound
    target -- merely authorizing a new owner is not enough (AT-F1 R08).
    """
    from common.models import Attachment

    if not row_matches_exact_target(row, target):
        return "target_mismatch"
    actor = fresh_actor(user)
    if actor is None or not fresh_role(actor, "work_order"):
        return "actor_changed"
    if target.get("model_type") == "part" and not fresh_role(actor, "part"):
        return "actor_changed"
    attachment = Attachment.objects.filter(pk=target.get("attachment_id")).first()
    if (
        attachment is None
        or attachment.model_type != target.get("model_type")
        or attachment.model_id != target.get("model_id")
    ):
        return "owner_changed"
    if not _exact_owner_authorized(actor, attachment):
        return "authorization_changed"
    if not exact_target_binding_current(user, target):
        return "binding_changed"
    return ""
