"""Approval executors owned by the maintenance tasks application."""

from django.contrib.auth import get_user_model

from approvals.executors import ApprovalExecutor, DriftReport, EffectResult
from approvals.models import ActionType
from tasks.models import Procedure, ProcedureRevision, ProcedureRevisionStatus
from tasks.services.procedures import (
    ProcedurePublishError,
    publish_revision,
    validate_publish_preconditions,
)

User = get_user_model()


class ProcedurePublishExecutor(ApprovalExecutor):
    """Revalidate and publish the exact procedure revision humans reviewed."""

    action_type = ActionType.PROCEDURE_PUBLISH
    required_fields = (
        'procedure_id',
        'revision_id',
        'revision_number',
        'content_version',
        'content_hash',
        'scope',
        'requested_by_id',
    )

    def validate(self, payload: dict) -> list[str]:
        """Return payload validation failures as executor warnings."""
        if not isinstance(payload, dict):
            return ['Payload must be an object']
        return [
            f'Missing required field: {field}'
            for field in self.required_fields
            if field not in payload
        ]

    def _load(self, payload: dict):
        """Load the approval actor and governed rows."""
        actor = User.objects.get(pk=payload['requested_by_id'])
        procedure = Procedure.objects.get(pk=payload['procedure_id'])
        revision = ProcedureRevision.objects.get(pk=payload['revision_id'])
        return actor, procedure, revision

    def check_preconditions(self, payload: dict, baseline_context: dict) -> DriftReport:
        """Report drift between the approved payload and current revision state."""
        del baseline_context
        validation_errors = self.validate(payload)
        if validation_errors:
            return DriftReport(
                has_drift=True,
                failed=[
                    {'check': 'payload', 'message': error}
                    for error in validation_errors
                ],
            )
        try:
            actor, procedure, revision = self._load(payload)
            validate_publish_preconditions(
                procedure=procedure,
                revision=revision,
                procedure_id=payload['procedure_id'],
                revision_number=payload['revision_number'],
                content_hash=payload['content_hash'],
                content_version=payload['content_version'],
                actor=actor,
                scope=payload['scope'],
            )
            if revision.status not in {
                ProcedureRevisionStatus.IN_REVIEW,
                ProcedureRevisionStatus.PUBLISHED,
            }:
                raise ProcedurePublishError('Revision is not in review')
        except (
            KeyError,
            TypeError,
            ValueError,
            ProcedurePublishError,
            Procedure.DoesNotExist,
            ProcedureRevision.DoesNotExist,
            User.DoesNotExist,
        ) as exc:
            return DriftReport(
                has_drift=True,
                failed=[{'check': 'procedure_revision', 'message': str(exc)}],
            )
        return DriftReport(
            has_drift=False,
            passed=[
                {
                    'check': 'procedure_revision',
                    'message': 'Approved revision is current',
                }
            ],
        )

    def execute(self, payload: dict, idempotency_key: str) -> EffectResult:
        """Publish the reviewed revision, returning failures without side effects."""
        del idempotency_key
        validation_errors = self.validate(payload)
        if validation_errors:
            return EffectResult(
                success=False, error_message='; '.join(validation_errors)
            )
        try:
            actor = User.objects.get(pk=payload['requested_by_id'])
            effect_ref = publish_revision(
                procedure_id=payload['procedure_id'],
                revision_id=payload['revision_id'],
                revision_number=payload['revision_number'],
                content_hash=payload['content_hash'],
                content_version=payload['content_version'],
                actor=actor,
                scope=payload['scope'],
            )
            return EffectResult(
                success=True,
                effect_ref=effect_ref,
                result_payload={
                    'procedure_id': payload['procedure_id'],
                    'revision_id': payload['revision_id'],
                    'revision_number': payload['revision_number'],
                    'content_hash': payload['content_hash'],
                    'content_version': payload['content_version'],
                },
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ProcedurePublishError,
            User.DoesNotExist,
        ) as exc:
            return EffectResult(success=False, error_message=str(exc))
