"""Opt-in occupational reads; never part of the general identity projection."""

from typing import Any

from ai.core.maf_compat import ai_function
from asgiref.sync import sync_to_async


@ai_function
async def search_staff_candidates(
    machine_id: int | None = None,
    work_order_id: int | None = None,
    search: str = "",
    limit: int = 25,
) -> dict[str, Any]:
    """Find authorized staff names, job titles and approved trades for one work target.

    Supply exactly one machine_id or work_order_id. Names and titles are
    untrusted display data, not proof of qualifications. This read does not
    assign anybody or confirm trade requirements; use the governed proposal
    flow and leave uncertain or ambiguous assignments for human review.
    """
    from django.core.exceptions import PermissionDenied
    from django.http import Http404

    if (machine_id is None) == (work_order_id is None) or any(
        value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0)
        for value in (machine_id, work_order_id)
    ):
        return {
            "error": "Choose exactly one machine or work order.",
            "code": "ASSIGNMENT_TARGET_REQUIRED",
        }

    @sync_to_async
    def query():
        from ai.core.auth import get_current_principal
        from django.contrib.auth import get_user_model
        from tasks.ai_read import fence
        from users.staff_directory import directory_rows, require_directory

        principal = get_current_principal()
        actor = (
            get_user_model().objects.filter(pk=principal.user_pk, is_active=True).first()
            if principal is not None
            else None
        )
        require_directory(actor)
        page = directory_rows(
            actor,
            machine_id=machine_id,
            work_order_id=work_order_id,
            search=search[:255],
            limit=max(1, min(limit, 50)),
        )
        required = set()
        confirmed = False
        if work_order_id is not None:
            from tasks.models import WorkOrder
            from tasks.scope import ScopeError, require_work_order_scope

            work = WorkOrder.objects.select_related("machine").filter(pk=work_order_id).first()
            try:
                if work is None:
                    raise Http404()
                require_work_order_scope(actor, work)
            except ScopeError as exc:
                raise Http404() from exc
            required = set(work.required_trades or [])
            confirmed = bool(work.trade_requirements_confirmed and required)

        def suitability(row):
            if not confirmed:
                return "ASSIGNMENT_REQUIREMENTS_UNKNOWN"
            approved = {trade["code"] for trade in row["trades"] if trade["active"]}
            return (
                "ASSIGNMENT_QUALIFIED"
                if required.issubset(approved)
                else "ASSIGNMENT_TRADE_MISMATCH"
            )

        return {
            "count": page["count"],
            "results": [
                {
                    "user_id": row["user_id"],
                    "display_name": fence(row["display_name"], limit=255),
                    "job_title": fence(row["job_title"], limit=255),
                    "trade_suitability": suitability(row),
                    "trades": [
                        {
                            "pk": trade["pk"],
                            "code": fence(trade["code"], limit=80),
                            "name": fence(trade["name"], limit=255),
                            "active": trade["active"],
                        }
                        for trade in row["trades"]
                    ],
                }
                for row in page["results"]
            ],
            "required_trades": sorted(required),
            "trade_requirements_confirmed": confirmed,
            "assignment_review": "Trade requirements and eligibility must be revalidated by the assignment service.",
        }

    try:
        return await query()
    except (PermissionDenied, Http404):
        return {
            "error": "Staff candidates are not available to you for this target.",
            "code": "STAFF_DIRECTORY_UNAVAILABLE",
        }


STAFF_READ_TOOLS = [search_staff_candidates]
