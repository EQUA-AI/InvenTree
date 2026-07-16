"""Maintenance execution permission constants and service helpers."""

from django.core.exceptions import PermissionDenied

PLAN_WORKORDER = 'tasks.plan_workorder'
ASSIGN_WORKORDER = 'tasks.assign_workorder'
TRANSITION_WORKORDER = 'tasks.transition_workorder'
EXECUTE_WORKORDER = 'tasks.execute_workorder'
COMPLETE_WORKORDER = 'tasks.complete_workorder'
AUTHOR_PROCEDURE = 'tasks.author_procedure'
REVIEW_PROCEDURE = 'tasks.review_procedure'
PUBLISH_PROCEDURE = 'tasks.publish_procedure'
APPLY_PROCEDURE = 'tasks.apply_procedure'
MANAGE_JOBKIT = 'tasks.manage_jobkit'
RESERVE_JOBKIT = 'tasks.reserve_jobkit'
STAGE_JOBKIT = 'tasks.stage_jobkit'
ISSUE_JOBKIT = 'tasks.issue_jobkit'
APPROVE_JOBKIT_SUBSTITUTION = 'tasks.approve_jobkit_substitution'
VIEW_WORKORDER_AUDIT = 'tasks.view_workorder_audit'


def require_permission(actor, codename: str) -> None:
    """Raise when an authenticated actor lacks a Django permission."""
    if actor is None or not getattr(actor, 'has_perm', lambda _perm: False)(codename):
        raise PermissionDenied(f'Missing required permission: {codename}')


def transition_permission(to_status: str) -> str:
    """Map a lifecycle destination to the controlling permission."""
    if to_status in {'in_progress', 'on_hold'}:
        return EXECUTE_WORKORDER
    if to_status == 'completed':
        return COMPLETE_WORKORDER
    if to_status in {'planned', 'ready', 'canceled', 'verifying'}:
        return TRANSITION_WORKORDER
    return TRANSITION_WORKORDER
