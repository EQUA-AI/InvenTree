"""Inventory and maintenance stages of a workbook population run.

These stages come after the plant exists: warehouse bins, manufacturer and
supplier identities, manufacturer product numbers for catalogue parts, book
stock, dated maintenance history and staged work orders. Like the plant
stages they are planned read-only first and applied inside the caller's
transaction, and replaying the same workbook changes nothing.

Three things a workbook is deliberately *not* allowed to do here:

* post a supplier offer without a supplier and a SKU (the package leaves
  both blank until a distributor listing is verified, QA-23);
* record history that claims a work order was closed (QA-26) - a closed job
  is produced by closing it;
* move a staged job past ``planned``. Jobs are created in ``draft`` and, when
  the sheet asks for it, planned through the lifecycle service so its
  readiness and permission rules decide, not the import.

Sheets the application cannot take from a spreadsheet - user accounts,
governed procedures, files that are not on disk - are counted and reported
as manual work rather than approximated.
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from django.core.exceptions import ValidationError

from .population_workbook import PopulationWorkbook

WORK_ORDER_NAMESPACE = uuid5(NAMESPACE_URL, 'aimms:population:work-order')
POPULATION_TAG = 'workbook-population'

COMPANY_FIELDS = ('description', 'website', 'is_manufacturer', 'is_supplier', 'active')
STOCK_LOCATION_FIELDS = ('description', 'structural')


class OperationsError(Exception):
    """A row cannot be applied; the message names the sheet and row."""


# -- helpers ------------------------------------------------------------------


def _company_changes(company, record):
    changes = {}
    for name in COMPANY_FIELDS:
        wanted = getattr(record, name)
        if isinstance(wanted, str) and not wanted:
            continue  # the workbook does not blank out what an operator typed
        if getattr(company, name) != wanted:
            changes[name] = wanted
    return changes


def _stock_location_changes(node, record):
    changes = {}
    for name in STOCK_LOCATION_FIELDS:
        if getattr(node, name) != getattr(record, name):
            changes[name] = getattr(record, name)
    return changes


def _attach_note(instance, title: str, content: str) -> None:
    """Record the workbook's explanatory text as the instance's primary note.

    Notes are their own rows (``common.Note``) rather than a text column, so
    the illustrative cost and the "estimate, not a quote" basis stay visible
    on the stock item without being mistaken for a purchase price.
    """
    if not content:
        return
    from django.contrib.contenttypes.models import ContentType

    from common.models import Note

    Note.objects.create(
        model_type=ContentType.objects.get_for_model(instance),
        model_id=instance.pk,
        primary=True,
        title=title[:100],
        content=content,
    )


def manual_work(book: PopulationWorkbook) -> dict:
    """What the workbook asks for that only a person can do in the application."""
    out = {}
    if book.manual.get('Staff Roles'):
        out['staff_roles'] = {
            'rows': book.manual['Staff Roles'],
            'note': (
                'User accounts are not created from a workbook; names appear as '
                'free-text assignee / performed_by until accounts exist.'
            ),
        }
    if book.manual.get('PM Plans'):
        out['pm_plans'] = {
            'rows': book.manual['PM Plans'],
            'note': (
                'Procedures are authored and published through the governed '
                'procedure workflow (customer-scoped); not imported.'
            ),
        }
    if book.manual.get('Attachment Plan'):
        out['attachments'] = {
            'rows': book.manual['Attachment Plan'],
            'note': 'No files ship with the package; attach from the archive by hand.',
        }
    packets = sum(1 for wo in book.work_orders if wo.repair_packet)
    if packets:
        out['repair_packets'] = {
            'rows': packets,
            'note': 'Raise the repair packet from each staged work order in the UI.',
        }
    offers = sum(1 for offer in book.offers if not offer.has_supplier_offer)
    if offers:
        out['supplier_offers'] = {
            'rows': offers,
            'note': 'No supplier or SKU verified; only the manufacturer part is created.',
        }
    return out


# -- planning -----------------------------------------------------------------


def plan_operations(plan, book: PopulationWorkbook, machines: dict) -> None:
    """Add the inventory and maintenance steps to ``plan`` without writing."""
    from tasks.models import WorkOrder

    from company.models import Company, ManufacturerPart, SupplierPart
    from part.models import Part
    from stock.models import StockItem, StockLocation

    from .models import AssetMaintenanceRecord

    # Warehouse tree ----------------------------------------------------------
    nodes: dict[str, StockLocation | None] = {}
    for record in book.stock_locations:
        parent = nodes.get(record.parent_code) if record.parent_code else None
        if record.parent_code and parent is None:
            node = None  # parent is new in this run, so the child must be too
        else:
            node = StockLocation.objects.filter(name=record.name, parent=parent).first()
        nodes[record.code] = node
        if node is None:
            plan.add('stock_location', record.code, 'create', record.name)
        elif _stock_location_changes(node, record):
            plan.add('stock_location', record.code, 'update')
        else:
            plan.add('stock_location', record.code, 'unchanged')

    # Companies -----------------------------------------------------------------
    companies: dict[str, Company | None] = {}
    for record in book.companies:
        company = Company.objects.filter(name=record.name).first()
        companies[record.name.casefold()] = company
        if company is None:
            plan.add('company', record.name, 'create')
        elif _company_changes(company, record):
            plan.add('company', record.name, 'update')
        else:
            plan.add('company', record.name, 'unchanged')

    # Manufacturer parts and offers -----------------------------------------------
    parts = {
        part.IPN.casefold(): part
        for part in Part.objects.filter(
            IPN__in=[o.part_ipn for o in book.offers]
            + [s.part_ipn for s in book.stock_items]
        )
    }
    for offer in book.offers:
        part = parts.get(offer.part_ipn.casefold())
        maker = companies.get(offer.manufacturer.casefold())
        key = f'{offer.part_ipn}:{offer.manufacturer}:{offer.mpn}'
        if part is None or maker is None:
            plan.add('manufacturer_part', key, 'create')
        elif ManufacturerPart.objects.filter(
            part=part, manufacturer=maker, MPN=offer.mpn
        ).exists():
            plan.add('manufacturer_part', key, 'exists')
        else:
            plan.add('manufacturer_part', key, 'create')
        if offer.has_supplier_offer:
            seller = companies.get(offer.supplier.casefold())
            if (
                part is not None
                and seller is not None
                and SupplierPart.objects.filter(
                    part=part, supplier=seller, SKU=offer.sku
                ).exists()
            ):
                plan.add('supplier_part', f'{offer.part_ipn}:{offer.sku}', 'exists')
            else:
                plan.add('supplier_part', f'{offer.part_ipn}:{offer.sku}', 'create')

    # Stock -----------------------------------------------------------------------
    for item in book.stock_items:
        part = parts.get(item.part_ipn.casefold())
        location = nodes.get(item.location_code)
        key = item.key or f'{item.part_ipn}@{item.location_code}'
        if (
            part is not None
            and location is not None
            and StockItem.objects.filter(
                part=part, location=location, batch=item.batch
            ).exists()
        ):
            plan.add('stock_item', key, 'exists')
        else:
            plan.add('stock_item', key, 'create', f'x{item.quantity}')

    # Maintenance history -----------------------------------------------------------
    owners = {m.pk for m in machines.values() if m is not None}
    existing_history = {
        (r.machine_id, r.date, r.summary.casefold()): r
        for r in AssetMaintenanceRecord.objects.filter(machine_id__in=owners)
    }
    for record in book.history:
        owner = machines.get(record.machine_key)
        key = record.key or f'{record.machine_key}:{record.date}'
        current = (
            existing_history.get((owner.pk, record.date, record.summary.casefold()))
            if owner
            else None
        )
        if current is None:
            plan.add('history', key, 'create')
        elif (
            current.details != record.details
            or current.performed_by != record.performed_by
        ):
            plan.add('history', key, 'update')
        else:
            plan.add('history', key, 'unchanged')

    # Work orders -----------------------------------------------------------------
    staged = {
        wo.job_number: wo
        for wo in WorkOrder.objects.filter(
            job_number__in=[record.key for record in book.work_orders]
        )
    }
    for record in book.work_orders:
        current = staged.get(record.key)
        if current is None:
            plan.add('work_order', record.key, 'create', record.target_status)
        elif current.lifecycle_status == 'draft' and record.target_status == 'planned':
            plan.add('work_order', record.key, 'plan')
        else:
            plan.add('work_order', record.key, 'exists', current.lifecycle_status)

    plan.manual = manual_work(book)


# -- applying -----------------------------------------------------------------


def apply_operations(
    book: PopulationWorkbook, actor, machines: dict, parts: dict, provenance: str
) -> dict:
    """Perform the inventory and maintenance stages inside the caller's transaction.

    ``parts`` maps case-folded IPN to ``Part`` for everything the catalogue
    stage created or found. ``provenance`` is the short run label written to
    notes and tracking entries.
    """
    from tasks.models import WorkOrder
    from tasks.services.work_orders import WorkOrderCommandError, transition_work_order

    from company.models import Company, ManufacturerPart, SupplierPart
    from part.models import Part
    from stock.models import StockItem, StockLocation

    from .models import AssetMaintenanceRecord

    out: dict = {
        'stock_locations': {'created': 0, 'updated': 0, 'unchanged': 0},
        'companies': {'created': 0, 'updated': 0, 'unchanged': 0},
        'manufacturer_parts': {'created': 0, 'existing': 0, 'skipped': 0},
        'supplier_parts': {'created': 0, 'existing': 0, 'blocked': 0},
        'stock_items': {'created': 0, 'existing': 0, 'skipped': 0},
        'history': {'created': 0, 'updated': 0, 'unchanged': 0, 'skipped': 0},
        'work_orders': {'created': 0, 'planned': 0, 'existing': 0, 'skipped': 0},
        'warnings': [],
        'manual': manual_work(book),
    }

    # Warehouse tree ----------------------------------------------------------
    nodes: dict[str, StockLocation] = {}
    for record in book.stock_locations:
        parent = nodes[record.parent_code] if record.parent_code else None
        node = StockLocation.objects.filter(name=record.name, parent=parent).first()
        try:
            if node is None:
                node = StockLocation(
                    name=record.name,
                    parent=parent,
                    description=record.description,
                    structural=record.structural,
                )
                node.full_clean()
                node.save()
                out['stock_locations']['created'] += 1
            else:
                changes = _stock_location_changes(node, record)
                if changes:
                    for name, value in changes.items():
                        setattr(node, name, value)
                    node.full_clean()
                    node.save()
                    out['stock_locations']['updated'] += 1
                else:
                    out['stock_locations']['unchanged'] += 1
        except ValidationError as exc:
            raise OperationsError(
                f'Warehouse location {record.code} (row {record.row}): '
                f'{"; ".join(exc.messages)}'
            ) from exc
        nodes[record.code] = node

    # Companies -----------------------------------------------------------------
    companies: dict[str, Company] = {}
    for record in book.companies:
        company = Company.objects.filter(name=record.name).first()
        try:
            if company is None:
                fields = {
                    'name': record.name,
                    'description': record.description,
                    'website': record.website,
                    'phone': record.phone,
                    'email': record.email or None,
                    'is_manufacturer': record.is_manufacturer,
                    'is_supplier': record.is_supplier,
                    'is_customer': False,
                    'active': record.active,
                }
                if record.currency:
                    fields['currency'] = record.currency
                company = Company(**fields)
                company.full_clean()
                company.save()
                out['companies']['created'] += 1
            else:
                changes = _company_changes(company, record)
                if changes:
                    for name, value in changes.items():
                        setattr(company, name, value)
                    company.full_clean()
                    company.save()
                    out['companies']['updated'] += 1
                else:
                    out['companies']['unchanged'] += 1
        except ValidationError as exc:
            raise OperationsError(
                f'Company {record.name} (row {record.row}): {"; ".join(exc.messages)}'
            ) from exc
        companies[record.name.casefold()] = company

    # Manufacturer parts and offers -----------------------------------------------
    missing = [
        ipn
        for ipn in {o.part_ipn.casefold() for o in book.offers}
        | {s.part_ipn.casefold() for s in book.stock_items}
        if ipn not in parts
    ]
    if missing:
        for part in Part.objects.filter(IPN__in=missing):
            parts[part.IPN.casefold()] = part
    for offer in book.offers:
        part = parts.get(offer.part_ipn.casefold())
        maker = companies.get(offer.manufacturer.casefold())
        if maker is None:
            maker = Company.objects.filter(
                name=offer.manufacturer, is_manufacturer=True
            ).first()
        if part is None or maker is None:
            out['manufacturer_parts']['skipped'] += 1
            out['warnings'].append(
                f'Offer row {offer.row}: part {offer.part_ipn} or manufacturer '
                f'{offer.manufacturer!r} not found; manufacturer part skipped.'
            )
            continue
        try:
            manufacturer_part, created = ManufacturerPart.objects.get_or_create(
                part=part,
                manufacturer=maker,
                MPN=offer.mpn,
                defaults={
                    'link': offer.link or None,
                    'description': offer.status[:250],
                },
            )
        except ValidationError as exc:
            raise OperationsError(
                f'Offer row {offer.row}: {"; ".join(exc.messages)}'
            ) from exc
        out['manufacturer_parts']['created' if created else 'existing'] += 1
        if not offer.has_supplier_offer:
            out['supplier_parts']['blocked'] += 1
            continue
        seller = (
            companies.get(offer.supplier.casefold())
            or Company.objects.filter(name=offer.supplier, is_supplier=True).first()
        )
        if seller is None:
            out['supplier_parts']['blocked'] += 1
            out['warnings'].append(
                f'Offer row {offer.row}: supplier {offer.supplier!r} not found.'
            )
            continue
        _, created = SupplierPart.objects.get_or_create(
            part=part,
            supplier=seller,
            SKU=offer.sku,
            defaults={
                'manufacturer_part': manufacturer_part,
                'link': offer.link or None,
            },
        )
        out['supplier_parts']['created' if created else 'existing'] += 1

    # Stock -----------------------------------------------------------------------
    for item in book.stock_items:
        part = parts.get(item.part_ipn.casefold())
        location = nodes.get(item.location_code)
        if part is None or location is None:
            out['stock_items']['skipped'] += 1
            out['warnings'].append(
                f'Stock row {item.row}: part {item.part_ipn} or bin '
                f'{item.location_code} not found; skipped.'
            )
            continue
        if StockItem.objects.filter(
            part=part, location=location, batch=item.batch
        ).exists():
            # Quantities are not re-synced on replay: a count correction is a
            # stock-take, which leaves its own tracking entry.
            out['stock_items']['existing'] += 1
            continue
        stock = StockItem(
            part=part,
            location=location,
            quantity=item.quantity,
            batch=item.batch,
            status=item.status,
        )
        try:
            stock.save(user=actor, notes=provenance)
        except ValidationError as exc:
            raise OperationsError(
                f'Stock row {item.row}: {"; ".join(exc.messages)}'
            ) from exc
        _attach_note(stock, provenance, item.notes)
        out['stock_items']['created'] += 1

    # Maintenance history -----------------------------------------------------------
    for record in book.history:
        owner = machines.get(record.machine_key)
        if owner is None:
            out['history']['skipped'] += 1
            continue
        entry, created = AssetMaintenanceRecord.objects.get_or_create(
            machine=owner,
            date=record.date,
            summary=record.summary,
            defaults={'details': record.details, 'performed_by': record.performed_by},
        )
        if created:
            out['history']['created'] += 1
        elif (
            entry.details != record.details or entry.performed_by != record.performed_by
        ):
            entry.details = record.details
            entry.performed_by = record.performed_by
            entry.save(update_fields=['details', 'performed_by', 'updated_at'])
            out['history']['updated'] += 1
        else:
            out['history']['unchanged'] += 1

    # Work orders -----------------------------------------------------------------
    for record in book.work_orders:
        owner = machines.get(record.machine_key)
        if owner is None:
            out['work_orders']['skipped'] += 1
            continue
        work_order = WorkOrder.objects.filter(job_number=record.key).first()
        if work_order is None:
            tags = [POPULATION_TAG]
            if record.scenario_id:
                tags.append(record.scenario_id)
            work_order = WorkOrder(
                title=record.title,
                description=record.description,
                status=WorkOrder.STATUS_BACKLOG,
                priority=record.priority,
                due_date=record.due_date,
                assignee=record.assignee,
                tags=tags,
                job_number=record.key,
                lifecycle_status='draft',
                work_order_type=record.work_order_type,
                machine=owner,
                requested_by=actor,
                estimated_minutes=record.estimated_minutes,
            )
            try:
                work_order.full_clean()
            except ValidationError as exc:
                raise OperationsError(
                    f'Work order {record.key} (row {record.row}): '
                    f'{"; ".join(exc.messages)}'
                ) from exc
            work_order.save()
            out['work_orders']['created'] += 1
        else:
            out['work_orders']['existing'] += 1
        if record.target_status == 'planned' and work_order.lifecycle_status == 'draft':
            try:
                transition_work_order(
                    work_order_id=work_order.pk,
                    to_status='planned',
                    actor=actor,
                    expected_version=work_order.lifecycle_version,
                    idempotency_key=str(
                        uuid5(
                            WORK_ORDER_NAMESPACE, f'{book.sha256}:{record.key}:planned'
                        )
                    ),
                    reason=provenance,
                )
                out['work_orders']['planned'] += 1
            except WorkOrderCommandError as exc:
                # The lifecycle service decides; the import does not override it.
                out['warnings'].append(f'Work order {record.key} left in draft: {exc}')
    return out


__all__ = ['OperationsError', 'apply_operations', 'manual_work', 'plan_operations']
