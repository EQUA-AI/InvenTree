"""Native source meaning for future-only attestation, not inferred old history."""

import hashlib
import json

from django.core.serializers.json import DjangoJSONEncoder

MAX_ATTESTED_BYTES = 1024 * 1024
MAX_ATTESTED_READS = 1024


def subject(kind, pk):
    """Load an exact native subject; identity alone grants no disclosure."""
    from tasks.models import WorkOrder

    from assets.models import AssetMaintenanceRecord
    from common.models import Attachment

    kinds = {
        'work': WorkOrder,
        'maintenance': AssetMaintenanceRecord,
        'attachment': Attachment,
    }
    if type(kind) is not str or kind not in kinds or type(pk) is not int or pk < 1:
        raise ValueError('An exact native subject is required')
    return kinds[kind].objects.filter(pk=pk).first()


def native_visible(actor, value):
    """Retain native resource scope independently of the attested client."""
    from tasks.models import WorkOrder
    from tasks.scope import (
        ScopeError,
        require_maintenance_record_scope,
        require_work_order_scope,
    )

    from ai.core.integrations.retrieval_authority import (
        fresh_role,
        native_attachment_owner,
    )
    from assets.models import AssetMaintenanceRecord

    if value is None or not fresh_role(actor, 'work_order'):
        return False
    try:
        if type(value) is WorkOrder:
            require_work_order_scope(actor, value)
            return True
        if type(value) is AssetMaintenanceRecord:
            require_maintenance_record_scope(actor, value)
            return True
        return native_attachment_owner(actor, value)
    except ScopeError:
        return False


def _fields(value):
    from django.db.models import FileField

    result = {}
    for field in value._meta.concrete_fields:
        # Attachment initialization may have wrapped the stored name already.
        # Bind that literal reference, never .url or the FieldFile object.
        if isinstance(field, FileField):
            result[field.attname] = getattr(value, field.attname).name
        else:
            result[field.attname] = value.__dict__.get(field.attname)
    return result


def material_digest(kind, pk):
    """Bind direct state, current owner links and bounded original bytes.

    Call only after independent native and client authority. File I/O belongs
    outside state ownership. Unsupported/oversize/faulted content is unqualified;
    links are represented literally and never fetched. This is not all-writer
    CDC, a globally atomic snapshot, or a guarantee against restore/ABA.
    """
    value = subject(kind, pk)
    if value is None:
        return None
    initial = _fields(value)
    body = {'kind': kind, 'fields': initial}
    if kind == 'attachment':
        from tasks.models import WorkOrder

        from assets.models import AssetMachine

        model = {'assetmachine': AssetMachine, 'workorder': WorkOrder}.get(
            value.model_type
        )
        if model is None:
            return None
        owner = model.objects.filter(pk=value.model_id).first()
        if owner is None:
            return None
        body['owner'] = _fields(owner)
        if value.attachment:
            digest, count = hashlib.sha256(), 0
            with value.attachment.storage.open(value.attachment.name, 'rb') as handle:
                for _ in range(MAX_ATTESTED_READS):
                    chunk = handle.read(min(65536, MAX_ATTESTED_BYTES - count + 1))
                    if type(chunk) is not bytes:
                        raise ValueError('Source bytes are unavailable')
                    count += len(chunk)
                    if count > MAX_ATTESTED_BYTES:
                        raise ValueError('Source content exceeds attestation bound')
                    if not chunk:
                        break
                    digest.update(chunk)
                else:
                    raise ValueError('Source read count exceeds attestation bound')
            body['bytes_sha256'] = digest.hexdigest()
    machine_id = getattr(value, 'machine_id', None)
    if machine_id is not None:
        from assets.models import AssetMachine

        machine = AssetMachine.objects.filter(pk=machine_id).first()
        if machine is None:
            return None
        body['machine'] = _fields(machine)
    final = subject(kind, pk)
    if final is None or _fields(final) != initial:
        return None
    encoded = json.dumps(
        body,
        cls=DjangoJSONEncoder,
        sort_keys=True,
        separators=(',', ':'),
        allow_nan=False,
    ).encode()
    if len(encoded) > 256 * 1024:
        raise ValueError('Source metadata exceeds attestation bound')
    return hashlib.sha256(encoded).hexdigest()


def observe_native_birth(
    sender, instance, *, created=False, raw=False, using='default', **kwargs
):
    """Capture only supported future ORM inserts; default-off does no DB work."""
    from django.apps import apps
    from django.conf import settings

    if (
        not created
        or raw
        or using != 'default'
        or getattr(settings, 'AIMMS_INTEGRATION_AUTHORITY_ENABLED', False) is not True
        or not apps.is_installed('integration_state')
    ):
        return
    from tasks.models import WorkOrder

    from assets.models import AssetMaintenanceRecord
    from common.models import Attachment
    from integration_state.native_authority import record_source_birth

    kind = {
        WorkOrder: 'work',
        AssetMaintenanceRecord: 'maintenance',
        Attachment: 'attachment',
    }.get(sender)
    if kind is not None:
        record_source_birth(kind, instance.pk)
