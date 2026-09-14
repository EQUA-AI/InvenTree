"""Deterministic, serialized projection of administrator roles to permissions."""

from dataclasses import dataclass

from django.contrib.auth.models import Group, Permission
from django.db import transaction

import structlog

from InvenTree.ready import canAppAccessDatabase
from users.models import RuleSet
from users.permissions import split_model
from users.ruleset import (
    RULESET_CHANGE_INHERIT,
    RULESET_CUSTOM_PERMISSIONS,
    RULESET_NAMES,
    get_ruleset_models,
)

logger = structlog.get_logger('inventree')
ACTIONS = ('view', 'add', 'change', 'delete')


@dataclass(frozen=True)
class RolePermissionPlan:
    """A read-only diff; callers can inspect it without saving rules or grants."""

    add_ids: tuple[int, ...]
    remove_ids: tuple[int, ...]
    add: tuple[str, ...]
    remove: tuple[str, ...]
    missing: tuple[str, ...]


def group_permission_plan(group: Group) -> RolePermissionPlan:
    """Compute the complete desired managed set before changing any permission.

    A grant from any role wins. Child inheritance is action-by-action from the
    effective parent grants, never yesterday's database snapshot. Permissions
    outside the role/inheritance mappings remain untouched.
    """
    database = group._state.db or 'default'
    rules = {r.name: r for r in RuleSet.objects.using(database).filter(group=group)}
    managed = set()
    desired = set()

    def include(model_name, codename, allowed):
        model, app = split_model(model_name)
        key = (app, model, codename)
        managed.add(key)
        if allowed:
            desired.add(key)

    for name, models in get_ruleset_models().items():
        role = rules.get(name) or RuleSet(group=group, name=name)
        for model_name in models:
            model, _app = split_model(model_name)
            for action in ACTIONS:
                include(model_name, f'{action}_{model}', getattr(role, f'can_{action}'))
        for field, (model_name, codename) in RULESET_CUSTOM_PERMISSIONS.get(
            name, {}
        ).items():
            include(model_name, codename, getattr(role, field))

    # Preserve the native per-action mapping; a parent's change grant does not
    # newly confer add/delete. Explicit BOM/Build grants still win independently.
    for parent, child in RULESET_CHANGE_INHERIT:
        for action in ACTIONS:
            parent_key = (parent, parent, f'{action}_{parent}')
            include(f'{parent}_{child}', f'{action}_{child}', parent_key in desired)

    permissions = {
        (p.content_type.app_label, p.content_type.model, p.codename): p.pk
        for p in Permission.objects.using(database).select_related('content_type')
    }
    existing = set(group.permissions.using(database).values_list('pk', flat=True))
    desired_ids = {permissions[key] for key in desired if key in permissions}
    managed_ids = {permissions[key] for key in managed if key in permissions}
    add_ids = desired_ids - existing
    remove_ids = (managed_ids & existing) - desired_ids

    def labels(ids):
        return tuple(
            sorted(
                f'{app}.{code}'
                for (app, _model, code), pk in permissions.items()
                if pk in ids
            )
        )

    return RolePermissionPlan(
        add_ids=tuple(sorted(add_ids)),
        remove_ids=tuple(sorted(remove_ids)),
        add=labels(add_ids),
        remove=labels(remove_ids),
        missing=tuple(
            sorted(
                f'{app}.{model}:{code}'
                for app, model, code in desired - permissions.keys()
            )
        ),
    )


def rebuild_all_permissions() -> None:
    """Rebuild each group's role projection under its own transaction/lock."""
    logger.info('Rebuilding permissions')
    for group in Group.objects.all().order_by('pk'):
        update_group_roles(group)


def update_group_roles(group: Group, debug: bool = False) -> None:
    """Apply one final diff, serialized with RuleSet.save and other rebuilds."""
    if not canAppAccessDatabase(allow_test=True):
        return

    database = group._state.db or 'default'
    with transaction.atomic(using=database):
        # RuleSet.save takes this same lock before changing role values.
        Group.objects.using(database).select_for_update().get(pk=group.pk)
        rules = RuleSet.objects.using(database).filter(group=group)
        rules.exclude(name__in=RULESET_NAMES).delete()
        existing_names = set(rules.values_list('name', flat=True))
        # All fields are default-off. Avoid RuleSet.save -> Group.save -> this
        # signal recursively publishing a half-created permission projection.
        RuleSet.objects.using(database).bulk_create([
            RuleSet(group=group, name=name)
            for name in RULESET_NAMES
            if name not in existing_names
        ])
        plan = group_permission_plan(group)
        if plan.missing:
            # Configuration/schema faults must not publish a partial grant diff.
            raise ValueError(
                'Role permission definitions are missing: ' + ', '.join(plan.missing)
            )
        if plan.add_ids:
            group.permissions.add(*plan.add_ids)
        if plan.remove_ids:
            group.permissions.remove(*plan.remove_ids)
        if debug:
            logger.debug(
                'Role projection group_id=%s added=%s removed=%s',
                group.pk,
                len(plan.add_ids),
                len(plan.remove_ids),
            )
