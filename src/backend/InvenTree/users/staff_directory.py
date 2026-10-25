"""Minimal, scoped occupational information shared by human and AI callers."""

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.utils.module_loading import import_string

from tasks.scope import (
    ScopeError,
    granted_client_scope_resolver,
    require_machine_scope,
    require_work_order_scope,
    scope_for_actor,
    scope_for_machine,
    scope_for_work_order,
    single_site_scope_resolver,
)

from assets.models import AssetMachine, Client
from users.models import Trade

DIRECTORY_PERMISSION = 'users.view_staff_directory'


def require_directory(actor):
    """Require an active, authenticated principal with a real directory grant."""
    current = User.objects.filter(pk=getattr(actor, 'pk', None), is_active=True).first()
    if (
        actor is None
        or not actor.is_authenticated
        or current is None
        or not current.has_perm(DIRECTORY_PERMISSION)
    ):
        raise PermissionDenied('Staff Directory permission is required.')


def _scopes(actor):
    try:
        return scope_for_actor(actor)
    except ScopeError as exc:
        raise PermissionDenied('Maintenance scope is unresolved.') from exc


def _people():
    return User.objects.select_related('profile').prefetch_related(
        'profile__trades', 'aimms_client_grants', 'groups__rule_sets'
    )


def _standard_scopes(person, client, active_clients, *, explicit_grants):
    from tasks.scope import MaintenanceScope

    can_view = person.is_superuser or any(
        rule.name == 'work_order' and rule.can_view
        for group in person.groups.all()
        for rule in group.rule_sets.all()
    )
    if not person.is_active or not can_view:
        return set()
    if explicit_grants:
        grants = {grant.client_id for grant in person.aimms_client_grants.all()}
        if grants:
            return {
                MaintenanceScope(customer_id=None, site_key=None, client_id=pk)
                for pk in grants.intersection(active_clients)
            }
    return (
        {MaintenanceScope(customer_id=None, site_key=None, client_id=client.pk)}
        if client
        else set()
    )


def visible_people(actor, *, target_scope=None, user_id=None, search=''):
    """Resolve personnel in a real shared scope; never infer it from a title."""
    require_directory(actor)
    actor_scopes = _scopes(actor)
    if target_scope is not None and target_scope not in actor_scopes:
        raise Http404('Assignment target not found.')
    people = _people()
    if user_id is not None:
        people = people.filter(pk=user_id)
    for term in search.split():
        people = people.filter(
            Q(username__icontains=term)
            | Q(first_name__icontains=term)
            | Q(last_name__icontains=term)
            | Q(profile__position__icontains=term)
        )
    resolver = getattr(settings, 'AIMMS_MAINTENANCE_SCOPE_RESOLVER', None)
    if isinstance(resolver, str):
        resolver = import_string(resolver)
    standard = resolver in (single_site_scope_resolver, granted_client_scope_resolver)
    client = (
        Client.objects.filter(
            code=getattr(settings, 'AIMMS_SINGLE_SITE_CLIENT_CODE', 'internal'),
            active=True,
        ).first()
        if standard
        else None
    )
    active_clients = (
        set(Client.objects.filter(active=True).values_list('pk', flat=True))
        if standard
        else set()
    )
    rows = []
    for person in people.order_by('first_name', 'last_name', 'pk'):
        if person.profile.type == 'bot':
            continue
        # Inactive, already-linked users may be read explicitly, but not selected.
        if not person.is_active and user_id is None:
            continue
        if person.pk == actor.pk:
            scopes = actor_scopes
        elif standard:
            scopes = _standard_scopes(
                person,
                client,
                active_clients,
                explicit_grants=resolver is granted_client_scope_resolver,
            )
            if not person.is_active:
                # An inactive account's grants remain its historical boundary.
                from tasks.scope import MaintenanceScope

                scopes = {
                    MaintenanceScope(
                        customer_id=None, site_key=None, client_id=g.client_id
                    )
                    for g in person.aimms_client_grants.all()
                }
                if not scopes and client:
                    scopes = {
                        MaintenanceScope(
                            customer_id=None, site_key=None, client_id=client.pk
                        )
                    }
        else:
            try:
                scopes = scope_for_actor(person)
            except ScopeError:
                continue
        if target_scope is not None:
            if target_scope not in scopes:
                continue
        elif not actor_scopes.intersection(scopes):
            continue
        rows.append(person)
    return rows


def can_view_occupation(actor, person):
    """Keep legacy serializers from bypassing the occupational read boundary."""
    if actor is not None and actor.pk == person.pk and actor.is_active:
        return True
    if actor is None or not actor.is_active or not actor.has_perm(DIRECTORY_PERMISSION):
        return False
    return bool(visible_people(actor, user_id=person.pk))


def person_row(person):
    """Only expose data needed to recognize an assignee."""
    return {
        'user_id': person.pk,
        'display_name': person.get_full_name().strip() or person.username,
        'job_title': person.profile.position or '',
        'trades': [
            {
                'pk': trade.pk,
                'code': trade.code,
                'name': trade.name,
                'active': trade.active,
            }
            for trade in person.profile.trades.all()
        ],
    }


def executor_ids():
    """Batch execution grants instead of issuing permission queries per row."""
    return set(
        User.objects.filter(
            Q(is_superuser=True)
            | Q(
                user_permissions__content_type__app_label='tasks',
                user_permissions__codename='execute_workorder',
            )
            | Q(
                groups__permissions__content_type__app_label='tasks',
                groups__permissions__codename='execute_workorder',
            )
        ).values_list('pk', flat=True)
    )


def directory_rows(
    actor,
    *,
    machine_id=None,
    work_order_id=None,
    user_id=None,
    search='',
    limit=50,
    offset=0,
    trade=None,
    kanban_card_id=None,
):
    """Return a bounded personnel projection for one authorized target."""
    from tasks.models import WorkOrder

    require_directory(actor)
    if machine_id and work_order_id:
        raise ValueError('Choose a machine or a work order, not both.')
    target = None
    linked_user_id = None
    try:
        if work_order_id:
            work = WorkOrder.objects.select_related('machine').get(pk=work_order_id)
            require_work_order_scope(actor, work)
            target = scope_for_work_order(work)
            linked_user_id = work.assigned_to_id
            if kanban_card_id is not None:
                from tasks.models import KanbanCard

                card = KanbanCard.objects.filter(
                    pk=kanban_card_id, work_order=work
                ).first()
                if card is None:
                    raise Http404('Assignment target not found.')
                linked_user_id = card.assigned_to_id or linked_user_id
        elif machine_id:
            machine = AssetMachine.objects.get(pk=machine_id)
            require_machine_scope(actor, machine)
            target = scope_for_machine(machine)
        elif kanban_card_id is not None:
            raise ValueError('A board task lookup requires a work order.')
    except (WorkOrder.DoesNotExist, AssetMachine.DoesNotExist, ScopeError) as exc:
        raise Http404('Assignment target not found.') from exc
    people = visible_people(
        actor, target_scope=target, user_id=user_id, search=search[:255]
    )
    people = [
        person for person in people if person.is_active or person.pk == linked_user_id
    ]
    if target is not None:
        executors = executor_ids()
        people = [
            person
            for person in people
            if (person.is_active and person.profile.active and person.pk in executors)
            or (user_id is not None and person.pk == linked_user_id)
        ]
    if trade:
        people = [
            person
            for person in people
            if any(
                tag.code == trade and tag.active for tag in person.profile.trades.all()
            )
        ]
    return {
        'count': len(people),
        'results': [
            person_row(person) for person in people[offset : offset + min(limit, 100)]
        ],
    }


def validate_trade_codes(codes):
    """Accept only explicit active catalogue codes, never guessed qualifications."""
    if (
        not isinstance(codes, list)
        or len(codes) > 20
        or any(not isinstance(code, str) for code in codes)
        or len(codes) != len(set(codes))
    ):
        raise ValueError('Required trades must be a unique list of at most 20 codes.')
    if set(codes) != set(
        Trade.objects.filter(code__in=codes, active=True).values_list('code', flat=True)
    ):
        raise ValueError('Required trades contain an unknown or inactive trade.')
    return sorted(codes)
