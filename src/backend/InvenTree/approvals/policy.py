"""Explicit owner-confirmed Phase C pilot policy; no implicit future actions."""

import os
from dataclasses import dataclass

from django.conf import settings

from .models import ActionType


@dataclass(frozen=True)
class ActionPolicy:
    """Governance hooks stay explicit even when the pilot requires no step-up."""

    second_approver: bool
    step_up: str
    governing_reference: str
    self_approval_allowed: bool
    minimum_risk_tier: int = 2


_PILOT = ActionPolicy(False, 'none', 'owner-confirmed-phase-c-2026-09-12', True)
ACTION_POLICIES = {
    ActionType.EMAIL: _PILOT,
    ActionType.PURCHASE_ORDER: _PILOT,
    ActionType.SALES_ORDER: _PILOT,
    ActionType.STOCK_UPDATE: _PILOT,
    ActionType.WORKFLOW: _PILOT,
    ActionType.NOTIFICATION: _PILOT,
    ActionType.SAFETY_GATE: ActionPolicy(
        False, 'none', 'owner-confirmed-phase-c-2026-09-12', True, 3
    ),
    ActionType.PROCEDURE_PUBLISH: _PILOT,
    ActionType.JOB_KIT_SUBSTITUTION: _PILOT,
    ActionType.REPAIR_WORK_PACKAGE: _PILOT,
}


class ApprovalPolicyError(Exception):
    """A governed action requires a supported policy or named screen handoff."""


def _configured_ids(name):
    raw = getattr(settings, name, os.environ.get(name, ''))
    if not isinstance(raw, (str, list, tuple, set)):
        return set()
    return {
        str(v).strip()
        for v in (raw.split(',') if isinstance(raw, str) else raw)
        if str(v).strip()
    }


def effective_risk_tier(approval):
    """A client-supplied lower tier cannot weaken a registered action policy."""
    policy = ACTION_POLICIES.get(approval.action_type)
    return max(approval.risk_tier, policy.minimum_risk_tier if policy else 3)


def require_policy(approval, *, actor, channel):
    """Fail closed on missing policy, step-up, second approver or unsafe route."""
    policy = ACTION_POLICIES.get(approval.action_type)
    if policy is None or not policy.governing_reference:
        raise ApprovalPolicyError(
            'No governing approval policy is registered for this action.'
        )
    if policy.second_approver or policy.step_up != 'none':
        raise ApprovalPolicyError(
            'This action requires a second reviewer or MFA re-authentication on screen.'
        )
    if not policy.self_approval_allowed:
        requester = (
            approval.revisions
            .filter(revision_number=0)
            .values_list('created_by_user_id', flat=True)
            .first()
        )
        if requester is None or requester == actor.pk:
            raise ApprovalPolicyError(
                'Requester identity must be resolved and a different reviewer must approve.'
            )
    if channel not in ('screen', 'voice'):
        raise ApprovalPolicyError('Unknown approval channel.')
    if channel == 'voice':
        from .review_sections import voice_eligibility

        if effective_risk_tier(approval) >= 3:
            raise ApprovalPolicyError('Tier-3 approval requires screen review.')
        eligible, reason = voice_eligibility(approval)
        if not eligible:
            raise ApprovalPolicyError(reason or 'Review this request on screen.')
        if effective_risk_tier(approval) >= 2 and not (
            os.environ.get('CONTAINER_APP_NAME') == 'aimms-experimental'
            and str(actor.pk) in _configured_ids('APPROVAL_VOICE_PILOT_ACTOR_IDS')
            and str(approval.pk) in _configured_ids('APPROVAL_VOICE_PILOT_REQUEST_IDS')
        ):
            raise ApprovalPolicyError(
                'This audio route is not qualified; approve on screen.'
            )
