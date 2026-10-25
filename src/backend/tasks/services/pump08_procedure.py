"""Author the supplied exercise definition; independent publication is separate."""

from decimal import Decimal

from django.db import transaction

from repair.pump08_exercise import PROCEDURE_CODE, PROCEDURE_NAME, STEP_DEFINITIONS
from tasks.models import Procedure, ProcedureApplicability
from tasks.permissions import AUTHOR_PROCEDURE, require_permission
from tasks.scope import enforce_machine_scope
from tasks.services.procedures import (
    create_draft_step,
    edit_draft_revision,
    next_draft_revision,
)
from tasks.services.work_orders import WorkOrderCommandError


@transaction.atomic
def prepare_procedure(machine, actor):
    """Create one scoped draft through native versioned authoring commands only."""
    require_permission(actor, AUTHOR_PROCEDURE)
    enforce_machine_scope(actor, machine)
    if not (
        machine.asset_type == 'pump'
        and machine.source_key == 'P8'
        and machine.client.code == 'internal'
        and machine.parent is not None
        and machine.parent.name == 'Cedar Creek Final Effluent Pump Station'
    ):
        raise WorkOrderCommandError(
            'The supplied specification is only for configured Cedar Creek Pump08.'
        )
    existing = (
        Procedure.objects
        .select_for_update()
        .filter(client=machine.client, code=PROCEDURE_CODE)
        .first()
    )
    if existing is not None:
        revision = existing.revisions.order_by('-revision').first()
        return existing, revision, False
    procedure = Procedure.objects.create(
        code=PROCEDURE_CODE,
        name=PROCEDURE_NAME,
        client=machine.client,
        created_by=actor,
        description='Controlled exercise only. Prepared measurements are not physical work, OEM limits or operating release. Independent safety and closeout authority remain separate.',
    )
    revision = next_draft_revision(procedure, actor)
    revision = edit_draft_revision(
        revision,
        actor,
        revision.content_version,
        work_order_type='corrective',
        change_summary='Owner-supplied October25 controlled-exercise specification; no physical acceptance.',
    )
    ProcedureApplicability.objects.create(revision=revision, machine=machine)
    for definition in STEP_DEFINITIONS:
        create_draft_step(
            revision,
            actor,
            revision.content_version,
            sequence=definition['sequence'],
            step_type='verification',
            title=definition['title'],
            instruction=definition['note'],
            required=True,
            value_type=definition['value_type'],
            unit=definition['unit'],
            min_value=None,
            max_value=Decimal('124.9') if definition['sequence'] == 2 else None,
            evidence_policy={'required': True},
        )
        revision.refresh_from_db()
    return procedure, revision, True
