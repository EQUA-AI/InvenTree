"""Append-only R8 source/ack evidence using the existing unique command ledger.

These commands prepare evidence only. They do not place orders, send email,
receive stock, reserve materials or change completed Closeout records.
"""

import hashlib

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from common.models import Attachment
from company.models import SupplierPart
from order.models import PurchaseOrder, PurchaseOrderLineItem
from tasks.jobkit_models import JobKit, JobKitLine, JobKitShortage
from tasks.permissions import MANAGE_JOBKIT, require_permission
from tasks.procurement_serializers import (
    AcknowledgmentSerializer,
    SourceSelectionSerializer,
)
from tasks.scope import require_machine_scope
from tasks.services.procedure_execution import _command_replay, _record_command
from tasks.services.work_orders import (
    WorkOrderCommandError,
    _canonical_hash,
    _locked_work_order,
    _require_scope,
    _require_version,
)
from users.permissions import check_user_role

SCHEMA_VERSION = 'r8.1'
SOURCE_SELECTED = 'PROC_SOURCE_SELECTED'
PO_LINKED = 'PROC_PO_LINKED'
EMAIL_RECORDED = 'PROC_PO_EMAIL_RECORDED'
PO_ACKNOWLEDGED = 'PROC_PO_ACKNOWLEDGED'
RECEIPT_RECORDED = 'PROC_RECEIPT_RECORDED'
RECEIPT_ALLOCATED = 'PROC_RECEIPT_ALLOCATED'
PROCUREMENT_EVENT_TYPES = (
    SOURCE_SELECTED,
    PO_LINKED,
    EMAIL_RECORDED,
    PO_ACKNOWLEDGED,
    RECEIPT_RECORDED,
    RECEIPT_ALLOCATED,
)


class ProcurementError(WorkOrderCommandError):
    """A scoped, stable procurement precondition failed."""

    code = 'PROCUREMENT_INVALID'


class ProcurementStale(ProcurementError):  # noqa: N818
    """Requirement, configuration or superseded decision changed."""

    code = 'PROCUREMENT_STALE'


def configuration_revision(work_order, line):
    """Fingerprint actual configuration, not an invented OEM revision number.

    This is an optimistic token, never technical-fit approval. Only the cited
    technical source can support fit. Unrelated placement/telemetry is excluded.
    """
    machine = work_order.machine
    components = []
    if machine:
        components = list(
            machine.components.order_by('pk').values(
                'id', 'code', 'part_id', 'status', 'provenance', 'reviewed_at'
            )
        )
    return _canonical_hash(
        'procurement_configuration',
        None,
        {
            'machine_id': work_order.machine_id,
            'client_id': machine.client_id if machine else None,
            'manufacturer': machine.manufacturer if machine else None,
            'model': machine.model if machine else None,
            'serial': machine.serial if machine else None,
            'profile': machine.profile if machine else None,
            'components': components,
            'affected_component_ref': work_order.affected_component_ref,
            'source_application_hash': line.kit.source_application_hash,
        },
    )


def requirement_revision(work_order, line):
    """Detect scope/deadline drift as well as explicit line-version edits."""
    return _canonical_hash(
        'procurement_requirement',
        None,
        {
            'version': line.version,
            'part_id': line.selected_part_id,
            'requested_part_id': line.requested_part_id,
            'quantity': str(line.required_quantity),
            'required': line.required,
            'source_snapshot': line.source_snapshot,
            'due_date': work_order.due_date,
        },
    )


def purchase_order_revision(po):
    """Native commercial baseline; later edits cannot inherit old acceptance."""
    return _canonical_hash(
        'procurement_po',
        None,
        {
            'supplier_id': po.supplier_id,
            'currency': str(po.currency),
            'target_date': po.target_date,
            'goods': list(
                po.lines.order_by('pk').values(
                    'pk',
                    'part_id',
                    'quantity',
                    'purchase_price',
                    'purchase_price_currency',
                    'discount',
                    'part__pack_quantity_native',
                    'part__part__units',
                )
            ),
            'charges': list(
                po.extra_lines.order_by('pk').values(
                    'pk', 'reference', 'quantity', 'price', 'price_currency', 'discount'
                )
            ),
        },
    )


def selection_is_current(event, work_order, line, *, configuration=None):
    """Retained decisions become stale; reads never silently reselect."""
    data = event.metadata
    return (
        data.get('machine_id') == work_order.machine_id
        and data.get('requirement_version') == line.version
        and data.get('requirement_revision') == requirement_revision(work_order, line)
        and data.get('configuration_revision')
        == (configuration or configuration_revision(work_order, line))
    )


def can_read_procurement(actor, work_order):
    """Shared Work does not grant purchasing or foreign machine evidence."""
    from tasks.scope import work_machine_authorized

    return check_user_role(actor, 'purchase_order', 'view') and work_machine_authorized(
        actor, work_order
    )


def event_matches_context(event, work_order):
    """Neither a Work reassignment nor a tenant move transfers old evidence."""
    return (
        event.metadata.get('machine_id') == work_order.machine_id
        and work_order.machine is not None
        and event.metadata.get('machine_client_id') == work_order.machine.client_id
    )


def _authorize(actor, work_order):
    if not (
        getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False)
        and getattr(settings, 'AIMMS_JOB_KITS_ENABLED', False)
    ):
        raise ProcurementError('Work orders and Job Kits must be enabled')
    if not actor or not actor.is_active or not actor.is_authenticated:
        raise PermissionDenied('An active named internal actor is required')
    require_permission(actor, MANAGE_JOBKIT)
    _require_scope(actor, work_order)
    require_machine_scope(actor, work_order.machine)
    if not check_user_role(actor, 'purchase_order', 'change'):
        raise PermissionDenied('Purchase Order change role is required')


def _line(work_order, data):
    kit = JobKit.objects.select_for_update().filter(work_order=work_order).first()
    if kit is None:
        raise ProcurementError('Work order has no Job Kit')
    line = (
        JobKitLine.objects
        .select_for_update()
        .filter(kit=kit, pk=data['kit_line_id'])
        .first()
    )
    if line is None:
        raise ProcurementError('Kit line does not belong to this Work')
    line.kit = kit
    return line


def _validate_provenance(work_order, data):
    provenance = data['provenance']
    if provenance['mode'] == 'recorded_fixture':
        namespace = provenance['fixture_namespace']
        if (
            f'r8-fixture:{namespace}' not in work_order.tags
            or (work_order.reference or '').replace('-', '') == 'WO003826'
        ):
            raise ProcurementError(
                'Fixture evidence requires an isolated fixture Work; never WO-003826'
            )


def _evidence(ref, work_order, *, po_id=None):
    allowed = {('workorder', work_order.pk), ('assetmachine', work_order.machine_id)}
    if po_id is not None:
        allowed.add(('purchaseorder', po_id))
    attachment = Attachment.objects.filter(pk=ref['attachment_id']).first()
    if (
        attachment is None
        or (attachment.model_type, attachment.model_id) not in allowed
    ):
        raise ProcurementError(
            'Evidence must belong to this Work, machine or affected PO'
        )
    if not attachment.attachment:
        raise ProcurementError(
            'An original retained PDF is required; URLs are insufficient'
        )
    digest = hashlib.sha256()
    try:
        with attachment.attachment.open('rb') as stream:
            prefix = stream.read(5)
            if prefix != b'%PDF-':
                raise ProcurementError('Evidence must be an original PDF')
            digest.update(prefix)
            for chunk in iter(lambda: stream.read(65536), b''):
                digest.update(chunk)
            stream.seek(0)
            try:
                if ref['page'] > len(PdfReader(stream).pages):
                    raise ProcurementError('Cited page is outside the original PDF')
            except (PdfReadError, ValueError) as exc:
                raise ProcurementError('Original PDF cannot be read') from exc
    except OSError as exc:
        raise ProcurementError('Evidence bytes are unavailable') from exc
    if digest.hexdigest() != ref['sha256']:
        raise ProcurementError('Evidence hash does not match the original PDF')


def _prepare(serializer_class, payload):
    serializer = serializer_class(data=payload)
    serializer.is_valid(raise_exception=True)
    return dict(serializer.data)


def _begin(actor, work_order_id, data, command):
    work_order = _locked_work_order(work_order_id)
    _authorize(actor, work_order)
    request_hash = _canonical_hash(command, actor, data)
    replay = _command_replay(work_order, command, data['operation_key'], request_hash)
    if replay is not None and not event_matches_context(replay, work_order):
        raise ProcurementStale('Operation belongs to a previous machine context')
    return work_order, request_hash, replay


def _record(actor, work_order, data, command, event_type, request_hash, **extra):
    return _record_command(
        work_order=work_order,
        actor=actor,
        command=command,
        event_type=event_type,
        idempotency_key=data['operation_key'],
        request_hash=request_hash,
        metadata={
            **data,
            **extra,
            'schema_version': SCHEMA_VERSION,
            'machine_id': work_order.machine_id,
            'machine_client_id': work_order.machine.client_id,
            'actor': {
                'pk': actor.pk,
                'label': actor.get_full_name() or actor.username,
                'kind': 'internal_person',
            },
            'authority': 'evidence_only',
            'approval_policy': 'named_approver_per_po',
        },
    )


@transaction.atomic
def select_source(*, work_order_id, actor, **payload):
    """Record the first decision or append an explicit superseding decision."""
    from decimal import Decimal

    data = _prepare(SourceSelectionSerializer, payload)
    command = 'procurement_select_source'
    work_order, request_hash, replay = _begin(actor, work_order_id, data, command)
    if replay is not None:
        return replay
    _require_version(work_order, data['expected_version'])
    if work_order.lifecycle_status in {'completed', 'canceled'}:
        raise ProcurementError('Terminal Work cannot acquire a new source decision')
    _validate_provenance(work_order, data)
    line = _line(work_order, data)
    if (
        line.version != data['requirement_version']
        or configuration_revision(work_order, line) != data['configuration_revision']
    ):
        raise ProcurementStale(
            'Requirement or configuration changed; refresh before selecting'
        )
    previous = (
        work_order.events
        .filter(
            event_type=SOURCE_SELECTED,
            metadata__kit_line_id=line.pk,
            metadata__machine_id=work_order.machine_id,
            metadata__machine_client_id=work_order.machine.client_id,
        )
        .order_by('-pk')
        .first()
    )
    if (previous.pk if previous else None) != data['supersedes_event_id']:
        raise ProcurementStale(
            'Selection must explicitly supersede the latest source decision'
        )
    for ref in [
        *data['technical_evidence'],
        *([data['deadline_source']] if data['deadline_source'] else []),
    ]:
        _evidence(ref, work_order)
    for offer in data['offers']:
        _evidence(offer['source'], work_order)
        supplier_part = (
            SupplierPart.objects
            .select_related('supplier', 'part')
            .filter(pk=offer['supplier_part_id'])
            .first()
        )
        if (
            supplier_part is None
            or supplier_part.part_id != line.selected_part_id
            or not supplier_part.active
            or not supplier_part.supplier.active
            or not supplier_part.supplier.is_supplier
            or not supplier_part.part.active
            or not supplier_part.part.purchaseable
        ):
            raise ProcurementError(
                'Offer must name an active purchasable SupplierPart for the required Part'
            )
        if (
            Decimal(offer['base_units_per_order_unit'])
            != supplier_part.pack_quantity_native
        ):
            raise ProcurementError(
                'Offer conversion must match the native SupplierPart pack quantity'
            )
        offer.update({
            'supplier_id': supplier_part.supplier_id,
            'part_id': supplier_part.part_id,
            'supplier_sku': supplier_part.SKU,
            'base_uom': supplier_part.part.units,
        })
    selected = next(
        offer for offer in data['offers'] if offer['key'] == data['selected_offer_key']
    )
    if (
        Decimal(selected['order_quantity'])
        * Decimal(selected['base_units_per_order_unit'])
        < line.required_quantity
    ):
        raise ProcurementError('Selected offer does not cover the requirement')
    return _record(
        actor,
        work_order,
        data,
        command,
        SOURCE_SELECTED,
        request_hash,
        requirement_revision=requirement_revision(work_order, line),
    )


@transaction.atomic
def acknowledge_order(*, work_order_id, actor, **payload):
    """Bind original acceptance to the actual linked native line and quantity."""
    from decimal import Decimal

    data = _prepare(AcknowledgmentSerializer, payload)
    command = 'procurement_acknowledge_order'
    work_order, request_hash, replay = _begin(actor, work_order_id, data, command)
    if replay is not None:
        return replay
    _require_version(work_order, data['expected_version'])
    _validate_provenance(work_order, data)
    line = _line(work_order, data)
    # Match native purchasing lock order: PO header before its line rows.
    po_id = (
        PurchaseOrderLineItem.objects
        .filter(pk=data['purchase_order_line_id'])
        .values_list('order_id', flat=True)
        .first()
    )
    po = PurchaseOrder.objects.select_for_update().filter(pk=po_id).first()
    po_line = (
        PurchaseOrderLineItem.objects
        .select_for_update()
        .filter(pk=data['purchase_order_line_id'], order=po)
        .first()
        if po
        else None
    )
    if (
        po_line is None
        or not JobKitShortage.objects.filter(
            line=line, purchase_order_line=po_line
        ).exists()
    ):
        raise ProcurementError(
            "Acknowledgment must identify this requirement's linked native PO line"
        )
    if (
        po_line.part is None
        or po_line.part.part_id != line.selected_part_id
        or po_line.part.supplier_id != po.supplier_id
    ):
        raise ProcurementError(
            'Acknowledged line does not match the required Part and order supplier'
        )
    if Decimal(data['accepted_quantity']) > po_line.quantity:
        raise ProcurementError('Acceptance exceeds the native ordered quantity')
    _evidence(data['source'], work_order, po_id=po.pk)
    return _record(
        actor,
        work_order,
        data,
        command,
        PO_ACKNOWLEDGED,
        request_hash,
        purchase_order_id=po.pk,
        supplier_part_id=po_line.part_id,
        order_quantity=str(po_line.quantity),
        # Native packaging is descriptive, not a guaranteed order UOM.
        order_uom=None,
        base_uom=po_line.part.part.units,
        base_units_per_order_unit=str(po_line.part.pack_quantity_native),
        supplier_id=po.supplier_id,
        order_revision=purchase_order_revision(po),
    )
