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
        return []
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
        if not set(row.get("client_codes") or ()).intersection(clients):
            continue
        accepted.append(row)
    return accepted


def controlled_rows(rows, *, user):
    """Site-wide controlled manuals retain their existing work-order reader role."""
    from ai.core.config import get_settings
    from aichat.models import ControlledDocument

    actor = fresh_actor(user)
    from InvenTree.restore_hold import restore_hold_enabled

    if (
        actor is None
        or not fresh_role(actor, "work_order")
        or len(rows) > 5
        or restore_hold_enabled()
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
        if attachment.model_type == "workorder":
            from tasks.models import WorkOrder

            work_order = (
                WorkOrder.objects.select_related("machine").filter(pk=attachment.model_id).first()
            )
            if work_order is None:
                return False
            require_work_order_scope(actor, work_order)
            return True
    except ScopeError:
        return False
    return False
