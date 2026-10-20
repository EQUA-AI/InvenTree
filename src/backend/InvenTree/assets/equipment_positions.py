"""R6 P07 / D02: what the reference pump model's positions stand for on a machine.

The Equipment view draws one authored reference model for every pump. Its
positions are not this machine's parts; each one stands for a component family
in the pump catalogue (``part/catalogues/pump_systems.json``), and the records
shown for a position are the machine's own records for that family:

- component slots: the machine's ``AssetComponent`` rows whose catalogue Part
  is the family's (IPN ``PS-<family>``), with their draft/verified status;
- signals: bindings whose dictionary point belongs to one of those slots, read
  through the Health tab's own ``signal_rows`` so values and states agree;
- work: open work in the reader's Work scope whose ``affected_component_ref``
  is one of those slot codes. Other open work on the machine is parent
  context, labelled as such, never attributed to the position.

A position with no family, or a family the machine has no slot for, shows
nothing for itself: absence is reported, not filled in from a neighbour.
"""

from __future__ import annotations

from collections import defaultdict

from tasks.dashboard_metrics import OPEN
from tasks.models import WorkOrder
from tasks.scope import ScopeError, work_order_scope_filter

from assets.health_models import MachineSignalBinding
from assets.registry_models import AssetComponent
from part.models import Part

REFERENCE_POSITIONS_RULE = 'reference_positions.v1'

#: Each reference-model position and the catalogue family it stands for, in
#: the model's own order. Confirmed by the project owner on 19 October 2026 as
#: close matches: the discharge position is the discharge line only (the HOPD
#: and EOPD valves are their own families). Coupling, seal, impeller and base
#: have no family yet. ``machine`` is the whole machine.
POSITION_FAMILIES: dict[str, str | None] = {
    'machine': None,
    'motor': 'MOTOR',
    'bearing': 'MOTOR-DE-BRG',
    'coupling': None,
    'seal': None,
    'impeller': None,
    'pump': 'CASING',
    'discharge': 'DISCHARGE',
    'base': None,
}

CATALOGUE_PREFIX = 'PS-'
#: Work rows listed per position; the count is always the full total.
MAX_WORK_ROWS = 10


def _work_row(work: WorkOrder) -> dict:
    owner = work.assigned_to
    return {
        'id': work.pk,
        'reference': work.reference,
        'title': work.title,
        'priority': work.priority,
        'lifecycle_status': work.lifecycle_status,
        'affected_component_ref': work.affected_component_ref,
        'owner': (owner.get_full_name() or owner.username) if owner else None,
    }


def machine_positions(actor, machine, *, now=None) -> dict:
    """The machine's records for each reference position, as defined above."""
    from machine_health.services.summary import signal_rows
    from part.management.commands.load_pump_catalogue import NAMESPACE

    families = sorted({family for family in POSITION_FAMILIES.values() if family})
    ipns = {CATALOGUE_PREFIX + family: family for family in families}
    # The family's name comes from the loader-owned catalogue Part only: IPNs
    # are not unique in InvenTree, and another Part may share one.
    parts = {
        ipns[part.IPN]: part
        for part in Part.objects.filter(IPN__in=ipns).order_by('pk')
        if (part.metadata or {}).get(NAMESPACE, {}).get('kind') == 'part'
    }
    # Slots match on the family's IPN, so a slot is found whichever Part with
    # that IPN it was registered against.
    components = list(
        AssetComponent.objects
        .filter(machine=machine, part__IPN__in=ipns)
        .select_related('part')
        .order_by('code')
    )
    from django.db.models import Prefetch, prefetch_related_objects

    from users.permissions import check_user_role

    from .recorded_facts import fit_projection, fit_queryset

    if check_user_role(actor, 'stock', 'view'):
        prefetch_related_objects(
            components,
            Prefetch('fits', queryset=fit_queryset(), to_attr='recorded_fits'),
        )
    by_family = defaultdict(list)
    for component in components:
        by_family[component.part.IPN[len(CATALOGUE_PREFIX) :]].append(component)

    component_of_binding = dict(
        MachineSignalBinding.objects.filter(
            machine=machine, active=True, dictionary_point__component__in=components
        ).values_list('pk', 'dictionary_point__component_id')
    )
    signals = defaultdict(list)
    if component_of_binding:
        for row in signal_rows(machine, now=now):
            component_id = component_of_binding.get(row['binding_id'])
            if component_id is not None:
                signals[component_id].append({
                    key: row[key]
                    for key in (
                        'binding_id',
                        'display_name',
                        'unit',
                        'value',
                        'observed_at',
                        'quality',
                        'stale',
                        'state',
                    )
                })

    try:
        open_work = WorkOrder.objects.filter(
            work_order_scope_filter(actor),
            machine=machine,
            is_active=True,
            lifecycle_status__in=OPEN,
        )
    except ScopeError:
        open_work = None
    work_by_code = defaultdict(list)
    if open_work is not None and components:
        for work in (
            open_work
            .filter(affected_component_ref__in=[c.code for c in components])
            .select_related('assigned_to')
            .order_by('-created_at', '-pk')
        ):
            work_by_code[work.affected_component_ref].append(work)

    positions = []
    for position, family in POSITION_FAMILIES.items():
        slots = by_family.get(family, []) if family else []
        jobs = [work for slot in slots for work in work_by_code.get(slot.code, [])]
        jobs.sort(key=lambda work: (work.created_at, work.pk), reverse=True)
        positions.append({
            'id': position,
            'family': family,
            'family_name': parts[family].name if family in parts else None,
            'components': [
                {
                    'fitted': fit_projection(slot.recorded_fits)
                    if hasattr(slot, 'recorded_fits')
                    else None,
                    'code': slot.code,
                    'name': slot.name,
                    'status': slot.status,
                    'provenance': slot.provenance,
                    'part': {
                        'pk': slot.part.pk,
                        'name': slot.part.name,
                        'IPN': slot.part.IPN,
                    },
                }
                for slot in slots
            ],
            'signals': [row for slot in slots for row in signals.get(slot.pk, [])],
            # Null when the reader's Work scope is unavailable or the position
            # has no slot to attribute work to: unknown, not zero.
            'work': None
            if open_work is None or not slots
            else {
                'count': len(jobs),
                'results': [_work_row(work) for work in jobs[:MAX_WORK_ROWS]],
            },
        })

    return {
        'rule': REFERENCE_POSITIONS_RULE,
        'machine': machine.pk,
        'machine_work': None if open_work is None else {'count': open_work.count()},
        'positions': positions,
    }
