"""Read-only native field/edge inventory; classification never grants access."""

import json
from dataclasses import fields, is_dataclass
from importlib import import_module

from django.apps import apps

SPECIFICATIONS = (
    (
        'assets.Client',
        (
            'assets.integration_contracts.ClientDetailsSourceRecord',
            'assets.integration_contracts.ClientStructureSourceRecord',
        ),
        {},
    ),
    (
        'assets.AssetLocation',
        ('assets.integration_contracts.LocationPropertiesSourceRecord',),
        {
            'client': 'assets.integration_sources.read_client_details',
            'parent': 'assets.integration_location_parents.read_location_parent',
        },
    ),
    (
        'assets.AssetMachine',
        (
            'assets.integration_contracts.MachineDetailsSourceRecord',
            'assets.integration_contracts.MachineRegistrationSourceRecord',
            'assets.integration_contracts.MachineBarcodeSourceRecord',
            'assets.integration_contracts.MachineProfileSourceRecord',
        ),
        {
            'client': 'assets.integration_machine_client.read_machine_client',
            'parent': 'assets.integration_machine_parent.read_machine_parent',
            'physical_location': 'assets.integration_machine_location.read_machine_placement',
        },
    ),
    (
        'tasks.WorkOrder',
        ('tasks.integration_s3_details.WorkDetailsSourceRecord',),
        {'machine': 'tasks.integration_s3_context.read_work_context'},
    ),
    (
        'assets.AssetMaintenanceRecord',
        ('tasks.integration_s3_details.MaintenanceDetailsSourceRecord',),
        {'work_order': 'assets.integration_work_links.read_maintenance_work_link'},
    ),
    (
        'common.Attachment',
        ('tasks.integration_s3_documents.DocumentMetadataSourceRecord',),
        {
            'model_type': 'tasks.integration_s3_documents.read_work_document',
            'model_id': 'tasks.integration_s3_documents.read_work_document',
            'attachment': 'tasks.integration_s3_documents.read_work_document_content',
            'link': 'tasks.integration_s3_documents.read_work_document',
        },
    ),
    ('repair.RepairPacket', (), {}),
    ('repair.RepairPacketGate', (), {}),
    ('repair.RepairPacketEvidence', (), {}),
    ('tasks.WorkOrderProcedureApplication', (), {}),
    ('tasks.WorkOrderStepExecution', (), {}),
    ('tasks.WorkOrderCloseout', (), {}),
    ('tasks.AcceptedCloseoutReport', (), {}),
)


def _resolve(path):
    module, name = path.rsplit('.', 1)
    return getattr(import_module(module), name)


def build_inventory():
    """Inspect actual model/DTO metadata only; never query source/client rows."""
    domains = []
    for label, contracts, edges in SPECIFICATIONS:
        model = apps.get_model(label)
        projected = set()
        withheld = set()
        descriptors = []
        for path in contracts:
            record = _resolve(path)
            if not is_dataclass(record):
                raise ValueError('A declared dataclass contract is required')
            names = [f.name for f in fields(record)]
            projected.update(names)
            descriptor = record.__dataclass_fields__.get('withheld_fields')
            if descriptor is not None:
                if type(descriptor.default) is not tuple:
                    raise ValueError('An explicit withheld-field policy is required')
                withheld.update(descriptor.default)
            descriptors.append({'contract': path, 'fields': names})
        for path in edges.values():
            if not callable(_resolve(path)):
                raise ValueError('A real relationship reader is required')
        if model._meta.label_lower == 'assets.assetmachine':
            for contract, marker, native_field in (
                (
                    'assets.integration_contracts.MachineRegistrationSourceRecord',
                    'source_context_state',
                    'source_context',
                ),
                (
                    'assets.integration_contracts.MachineBarcodeSourceRecord',
                    'barcode_data_state',
                    'barcode_data',
                ),
            ):
                if (
                    _resolve(contract).__dataclass_fields__[marker].default
                    != 'withheld'
                ):
                    raise ValueError('Native protected-field policy changed')
                withheld.add(native_field)
        native = []
        for f in model._meta.get_fields():
            state = (
                'independent_projection'
                if f.name in edges
                else 'direct_projection'
                if f.name in projected
                else 'identity_only'
                if getattr(f, 'primary_key', False)
                else 'explicitly_withheld'
                if f.name in withheld
                else 'no_reviewed_projection'
            )
            if (
                f.related_model is not None
                and f.related_model._meta.app_label == 'integration_state'
            ):
                state = 'private_integration_state_withheld'
            native.append({
                'name': f.name,
                'storage_name': getattr(f, 'attname', None),
                'concrete': f.concrete,
                'collection': f.many_to_many or f.one_to_many,
                'reverse': f.auto_created and not f.concrete,
                'type': f.get_internal_type()
                if hasattr(f, 'get_internal_type')
                else type(f).__name__,
                'nullable': getattr(f, 'null', None),
                'relation': None
                if f.related_model is None
                else f.related_model._meta.label_lower,
                'state': state,
                'reader': edges.get(f.name),
            })
        domains.append({
            'model': model._meta.label_lower,
            'contracts': descriptors,
            'fields': native,
            'policy_gaps': [
                f['name'] for f in native if f['state'] == 'no_reviewed_projection'
            ],
            'source_owner_reviewed': False,
        })
    return {
        'schema': 'aimms.native-contract-audit.v1',
        'kind': 'source_metadata_inventory',
        'queries_source_rows': False,
        'app_registry': sorted(app.label for app in apps.get_app_configs()),
        'confers_authority': False,
        'complete_semantic_acceptance': False,
        'domains': domains,
        'limits': 'All listed native fields are registered; unreviewed fields remain explicit policy gaps. Reader existence is not semantic approval, historical proof, deployed population completeness or a user grant.',
    }


def check_baseline(current, baseline):
    """Reject missing/changed model/contract fields rather than hiding drift."""

    def vector(value):
        if value.get('schema') != 'aimms.native-contract-audit.v1':
            raise ValueError('Unknown inventory schema')
        rows = value['domains']
        models = [r['model'] for r in rows]
        if len(models) != len(set(models)):
            raise ValueError('Duplicate native model')
        return [{k: r[k] for k in ('model', 'contracts', 'fields')} for r in rows]

    if vector(current) != vector(baseline):
        raise ValueError(
            'Native source/contract inventory changed; explicit reconciliation is required'
        )
    return True


def encode_inventory(value):
    """Use stable UTF-8 metadata for source binding and reviewer diffs."""
    return json.dumps(value, indent=2, sort_keys=True) + '\n'
