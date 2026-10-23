"""R6 P22: one job's material facts, from requirement to recorded use.

Read-only. Each fact keeps its own record and its own recorder: a requirement
is not a reservation, a reservation is not an issue, an issue is not a use, and
a use is not proof of what is fitted. Where the deployment has no Job Kit
(``AIMMS_JOB_KITS_ENABLED`` off, or no kit for the job) the job's requirement
rows are shown with their stock *check* - computed from on-hand stock, never a
reservation of an item. Stock-item identity (serial, batch, location) is only
given to readers with the stock view role.
"""

from django.conf import settings

from users.permissions import check_user_role

TERMINAL = ('consumed', 'returned', 'released', 'exception')


def can_read_work_stock(actor, work_order):
    """Stock identity requires stock view and the Work machine's client scope.

    Shared customer Work is not a grant to inspect another client's stock
    reservations, serials, locations or fitted-item records.
    """
    from tasks.scope import work_machine_authorized

    if not check_user_role(actor, 'stock', 'view'):
        return False
    return work_machine_authorized(actor, work_order)


def _person(user):
    """A recorder's display name, or None when nobody is recorded."""
    if user is None:
        return None
    return user.get_full_name() or user.username


def _when(value):
    return value.isoformat() if value else None


def _number(value):
    return None if value is None else float(value)


def _part(part):
    if part is None:
        return None
    return {
        'pk': part.pk,
        'name': part.name,
        'ipn': part.IPN or '',
        'units': part.units or '',
        'virtual': part.virtual,
    }


def _stock(item, visible):
    """Exact stock item; identity only for stock readers."""
    if item is None:
        return None
    if not visible:
        return {'pk': None, 'hidden': True}
    location = item.location
    return {
        'pk': item.pk,
        'hidden': False,
        'serial': item.serial or '',
        'batch': item.batch or '',
        'location': location.pathstring if location else '',
        'location_pk': location.pk if location else None,
    }


def _tracking(ids):
    """Native stock-tracking entries (who and when) by id."""
    from stock.models import StockItemTracking

    if not ids:
        return {}
    return {
        row.pk: row
        for row in StockItemTracking.objects.filter(pk__in=ids).select_related('user')
    }


def _usage_row(row, visible, work_order):
    from assets.recorded_facts import fit_row

    fit = getattr(row, 'componentfit', None) if visible else None
    if fit is not None and fit.component.machine_id != work_order.machine_id:
        fit = None
    return {
        'fitted_record': fit_row(fit) if fit else None,
        'id': row.pk,
        'allocation': row.allocation_id,
        'stock_tracking_id': row.stock_tracking_id if visible else None,
        'part': _part(row.part),
        'stock_item': _stock(row.stock_item, visible),
        'planned_quantity': _number(row.planned_quantity),
        'issued_quantity': _number(row.issued_quantity),
        'used_quantity': _number(row.used_quantity),
        'disposition': row.disposition or None,
        'variance_reason': row.variance_reason or '',
        'source': row.source,
        'candidate_text': row.candidate_text or '',
        'state': row.state,
        'resolved_by': _person(row.resolved_by),
        'updated_at': _when(row.updated_at),
    }


def _component(work_order, actor):
    """The registry slot the job names, when the machine holds one."""
    from assets.registry_models import AssetComponent

    ref = (work_order.affected_component_ref or '').strip()
    if not ref:
        return None
    from tasks.scope import work_machine_authorized

    slot = (
        (
            AssetComponent.objects
            .filter(machine_id=work_order.machine_id, code=ref)
            .only('pk', 'code', 'name', 'machine_id')
            .first()
        )
        if work_machine_authorized(actor, work_order)
        else None
    )
    return {
        'ref': ref,
        'pk': slot.pk if slot else None,
        'name': slot.name if slot else '',
        'machine': work_order.machine_id,
    }


def material_readout(actor, work_order) -> dict:
    """Requirements, reservations, custody and recorded use for one job."""
    from tasks.closeout_models import CloseoutPartUsage
    from tasks.jobkit_models import JobKit
    from tasks.procurement_readout import procurement_context
    from tasks.services.procurement import can_read_procurement

    visible = can_read_work_stock(actor, work_order)
    purchasing_visible = can_read_procurement(actor, work_order)
    kits_enabled = bool(getattr(settings, 'AIMMS_JOB_KITS_ENABLED', False))

    usage_query = CloseoutPartUsage.objects.filter(work_order=work_order)
    if visible:
        usage_query = usage_query.select_related(
            'componentfit__component',
            'componentfit__recorded_by',
            'componentfit__verified_by',
            'componentfit__retracted_by',
        )
    usage = list(
        usage_query.select_related(
            'part', 'stock_item__location', 'resolved_by'
        ).order_by('pk')
    )
    usage_by_allocation = {}
    loose_usage = []
    for row in usage:
        if row.allocation_id:
            usage_by_allocation.setdefault(row.allocation_id, []).append(row)
        else:
            loose_usage.append(_usage_row(row, visible, work_order))

    kit = None
    if kits_enabled:
        kit = (
            JobKit.objects
            .filter(work_order=work_order)
            .prefetch_related(
                'lines__requested_part',
                'lines__selected_part',
                'lines__shortages',
                'lines__allocations__stock_item__location',
                'lines__allocations__reserved_by',
                'lines__allocations__staged_by',
                'lines__allocations__issued_by',
            )
            .first()
        )

    requirements = []
    if kit is not None:
        lines = sorted(kit.lines.all(), key=lambda line: line.sequence)
        tracking = _tracking([
            allocation.stock_tracking_id
            for line in lines
            for allocation in line.allocations.all()
            if allocation.stock_tracking_id
        ])
        for line in lines:
            part = line.selected_part or line.requested_part
            reservations = []
            for allocation in sorted(line.allocations.all(), key=lambda a: a.pk):
                track = tracking.get(allocation.stock_tracking_id)
                reservations.append({
                    'id': allocation.pk,
                    'quantity': float(allocation.quantity),
                    'status': allocation.status,
                    'stock_item': _stock(allocation.stock_item, visible),
                    'reserved': {
                        'by': _person(allocation.reserved_by),
                        'at': _when(allocation.reserved_at),
                    },
                    'staged': (
                        {
                            'by': _person(allocation.staged_by),
                            'at': _when(allocation.staged_at),
                        }
                        if allocation.staged_at
                        else None
                    ),
                    'issued': (
                        {
                            'by': _person(allocation.issued_by),
                            'at': _when(allocation.issued_at),
                        }
                        if allocation.issued_at
                        else None
                    ),
                    'closed': (
                        {
                            'status': allocation.status,
                            'by': _person(track.user) if track else None,
                            'at': _when(allocation.disposed_at),
                            'tracking_id': allocation.stock_tracking_id
                            if visible
                            else None,
                        }
                        if allocation.status in TERMINAL
                        else None
                    ),
                    'usage': [
                        _usage_row(row, visible, work_order)
                        for row in usage_by_allocation.get(allocation.pk, [])
                    ],
                })
            requirements.append({
                'key': str(line.key),
                'part': _part(part),
                'requested_part': (
                    _part(line.requested_part)
                    if line.selected_part_id
                    and line.selected_part_id != line.requested_part_id
                    else None
                ),
                'quantity': float(line.required_quantity),
                'required': line.required,
                'fit_basis': {
                    'source': line.source,
                    'substitution_policy': line.substitution_policy,
                    'note': line.note or '',
                },
                'reservations': reservations,
                'shortages': [
                    {
                        'id': shortage.pk,
                        'quantity': float(shortage.quantity),
                        'status': shortage.status,
                        'purchase_order_line': (
                            shortage.purchase_order_line_id
                            if purchasing_visible
                            else None
                        ),
                        'reason': shortage.reason or '',
                    }
                    for shortage in line.shortages.all()
                ],
                'availability_check': None,
            })
    else:
        for row in work_order.work_order_parts.select_related('part').order_by(
            'created_at', 'pk'
        ):
            requirements.append({
                'key': f'requirement-{row.pk}',
                'part': _part(row.part),
                'requested_part': None,
                'quantity': float(row.quantity),
                'required': True,
                'fit_basis': None,
                # Not recorded: no stock item is reserved to this requirement.
                'reservations': None,
                'shortages': None,
                'availability_check': {
                    'status': row.allocation_status,
                    'quantity': float(row.allocated_quantity),
                    'note': row.allocation_note or '',
                    'checked_at': _when(row.updated_at),
                },
            })

    return {
        'work_order': work_order.pk,
        'source': 'job_kit' if kit is not None else 'requirements',
        'kits_enabled': kits_enabled,
        'stock_visible': visible,
        'component': _component(work_order, actor),
        'requirements': requirements,
        'unmatched_usage': loose_usage,
        'procurement': procurement_context(actor, work_order, stock_visible=visible),
    }


def closeout_material_context(actor, work_order):
    """Join usage to exact receipt stock/requirement, never guess by Part or PO.

    Costs are whole native PO totals, not an allocation of freight to usage.
    Live supporting facts stay separate from immutable completed report snapshots.
    """
    readout = material_readout(actor, work_order)
    rows = {row['id']: {**row, 'receipts': []} for row in readout['unmatched_usage']}
    lines = {line['key']: line for line in readout['procurement'].get('lines', [])}
    for requirement in readout['requirements']:
        line = lines.get(requirement['key'], {})
        orders = {
            order['purchase_order_line_id']: order for order in line.get('orders', [])
        }
        for allocation in requirement['reservations'] or []:
            for usage in allocation['usage']:
                receipts = []
                for event in line.get('receipts', []):
                    snapshot = event['snapshot']
                    stocks = [
                        stock
                        for stock in snapshot['stocks']
                        if stock['stock_item'] == (usage['stock_item'] or {}).get('pk')
                    ]
                    order = orders.get(snapshot['purchase_order_line_id'])
                    if not stocks or order is None:
                        continue
                    receipts.append({
                        'event_id': event['event_id'],
                        'recorded_at': event['recorded_at'],
                        'actor': event['actor'],
                        'stocks': stocks,
                        'purchase_order_id': order['purchase_order_id'],
                        'purchase_order_line_id': order['purchase_order_line_id'],
                        'reference': order['reference'],
                        'goods_total': order['goods_total'],
                        'charges_total': order['charges_total'],
                        'order_total': order['order_total'],
                        'currency': order['currency'],
                    })
                rows[usage['id']] = {
                    **usage,
                    'issued': allocation['issued'],
                    'receipts': receipts,
                }
    return rows
