"""REST API for governed procedure authoring and review."""

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404

from rest_framework import status
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from approvals.models import ActionType, Approval

from .models import Procedure, ProcedureRevision, ProcedureRevisionStatus
from .permissions import AUTHOR_PROCEDURE, PUBLISH_PROCEDURE, require_permission
from .procedure_serializers import (
    ArchiveProcedureRevisionSerializer,
    CreateDraftRevisionSerializer,
    EditDraftResourceSerializer,
    EditDraftRevisionSerializer,
    EditDraftStepSerializer,
    ProcedureBlockerSerializer,
    ProcedureResourceRequirementSerializer,
    ProcedureRevisionSerializer,
    ProcedureSerializer,
    ProcedureStepSerializer,
    PublishProcedureSerializer,
    ReorderStepsSerializer,
    RequestReviewSerializer,
)
from .scope import ScopeError, scope_for_actor
from .services.procedures import (
    ProcedureCommandError,
    ProcedureImmutableError,
    ProcedureStaleVersionError,
    create_draft_resource,
    create_draft_step,
    edit_draft_resource,
    edit_draft_revision,
    edit_draft_step,
    next_draft_revision,
    reorder_draft_steps,
    request_review,
    review_blockers,
)


class ProcedureEnabledMixin:
    """Hide the additive API unless enabled by deployment configuration."""

    def dispatch(self, request, *args, **kwargs):
        """Dispatch."""
        if not getattr(settings, 'AIMMS_PROCEDURES_ENABLED', False):
            raise Http404
        return super().dispatch(request, *args, **kwargs)


def _scoped_procedures(actor):
    """Apply explicit customer scope before every procedure lookup."""
    try:
        customer_ids = [
            item.customer_id for item in scope_for_actor(actor) if item.site_key is None
        ]
    except ScopeError:
        return Procedure.objects.none()
    return Procedure.objects.filter(customer_id__in=customer_ids)


def _procedure_queryset(actor):
    return _scoped_procedures(actor).select_related(
        'customer', 'current_revision', 'created_by'
    )


def _revision_queryset(actor):
    return ProcedureRevision.objects.filter(
        procedure__in=_scoped_procedures(actor)
    ).select_related('procedure', 'created_by', 'reviewed_by', 'published_by')


def _error_response(exc):
    if isinstance(exc, (ProcedureImmutableError, ProcedureStaleVersionError)):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, PermissionDenied):
        code = status.HTTP_403_FORBIDDEN
    else:
        code = status.HTTP_400_BAD_REQUEST
    return Response({'detail': str(exc)}, status=code)


def _approval_data(approval):
    return {
        'id': str(approval.pk),
        'status': approval.status,
        'action_type': approval.action_type,
        'summary': approval.summary,
        'payload': approval.payload,
        'created_at': approval.created_at,
    }


class ProcedureList(ProcedureEnabledMixin, APIView):
    """Procedure list."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Handle GET requests."""
        rows = _procedure_queryset(request.user).order_by('code', 'pk')
        return Response(ProcedureSerializer(rows, many=True).data)

    def post(self, request):
        """Handle POST requests."""
        serializer = ProcedureSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            require_permission(request.user, AUTHOR_PROCEDURE)
            customer = serializer.validated_data.get('customer')
            if customer is None or customer.pk not in {
                scope.customer_id for scope in scope_for_actor(request.user)
            }:
                raise ScopeError('Procedure scope is unresolved or unauthorized')
            procedure = serializer.save(created_by=request.user)
        except ScopeError as exc:
            raise Http404 from exc
        except PermissionDenied as exc:
            return _error_response(exc)
        return Response(
            ProcedureSerializer(procedure).data, status=status.HTTP_201_CREATED
        )


class ProcedureDetail(ProcedureEnabledMixin, APIView):
    """Procedure detail."""

    permission_classes = [IsAuthenticated]

    def get_object(self, request, pk):
        """Get object."""
        return get_object_or_404(_procedure_queryset(request.user), pk=pk)

    def get(self, request, pk):
        """Handle GET requests."""
        return Response(ProcedureSerializer(self.get_object(request, pk)).data)

    def patch(self, request, pk):
        """Handle PATCH requests."""
        procedure = self.get_object(request, pk)
        serializer = ProcedureSerializer(procedure, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            require_permission(request.user, AUTHOR_PROCEDURE)
            candidate_customer = serializer.validated_data.get(
                'customer', procedure.customer
            )
            if candidate_customer.pk not in {
                scope.customer_id for scope in scope_for_actor(request.user)
            }:
                raise ScopeError('Procedure scope is unauthorized')
            serializer.save()
        except ScopeError as exc:
            raise Http404 from exc
        except PermissionDenied as exc:
            return _error_response(exc)
        return Response(serializer.data)


class ProcedureRevisionList(ProcedureEnabledMixin, APIView):
    """Procedure revision list."""

    permission_classes = [IsAuthenticated]
    serializer_class = CreateDraftRevisionSerializer

    def get(self, request, pk):
        """Handle GET requests."""
        procedure = get_object_or_404(_procedure_queryset(request.user), pk=pk)
        rows = procedure.revisions.order_by('-revision')
        return Response(ProcedureRevisionSerializer(rows, many=True).data)

    def post(self, request, pk):
        """Handle POST requests."""
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        procedure = get_object_or_404(_procedure_queryset(request.user), pk=pk)
        try:
            revision = next_draft_revision(procedure, request.user)
        except (ProcedureCommandError, PermissionDenied) as exc:
            return _error_response(exc)
        except ScopeError as exc:
            raise Http404 from exc
        return Response(
            ProcedureRevisionSerializer(revision).data, status=status.HTTP_201_CREATED
        )


class ProcedureRevisionDetail(ProcedureEnabledMixin, APIView):
    """Procedure revision detail."""

    permission_classes = [IsAuthenticated]

    def get_object(self, request, pk):
        """Get object."""
        return get_object_or_404(_revision_queryset(request.user), pk=pk)

    def get(self, request, pk):
        """Handle GET requests."""
        return Response(ProcedureRevisionSerializer(self.get_object(request, pk)).data)

    def patch(self, request, pk):
        """Handle PATCH requests."""
        revision = self.get_object(request, pk)
        # Explicitly reject generic content edits to frozen definitions.
        if revision.status != ProcedureRevisionStatus.DRAFT:
            return _error_response(
                ProcedureImmutableError('Procedure revision content is immutable')
            )
        serializer = EditDraftRevisionSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        expected = values.pop('expected_content_version')
        try:
            revision = edit_draft_revision(revision, request.user, expected, **values)
        except (ProcedureCommandError, PermissionDenied) as exc:
            return _error_response(exc)
        except ScopeError as exc:
            raise Http404 from exc
        return Response(ProcedureRevisionSerializer(revision).data)


class ProcedureStepList(ProcedureEnabledMixin, APIView):
    """Procedure step list."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        """Handle GET requests."""
        revision = get_object_or_404(_revision_queryset(request.user), pk=pk)
        return Response(
            ProcedureStepSerializer(revision.steps.order_by('sequence'), many=True).data
        )

    def post(self, request, pk):
        """Handle POST requests."""
        revision = get_object_or_404(_revision_queryset(request.user), pk=pk)
        serializer = EditDraftStepSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        expected = values.pop('expected_content_version')
        try:
            step = create_draft_step(revision, request.user, expected, **values)
        except (ProcedureCommandError, PermissionDenied) as exc:
            return _error_response(exc)
        except ScopeError as exc:
            raise Http404 from exc
        return Response(
            ProcedureStepSerializer(step).data, status=status.HTTP_201_CREATED
        )


class ProcedureStepDetail(ProcedureEnabledMixin, APIView):
    """Procedure step detail."""

    permission_classes = [IsAuthenticated]

    def get_objects(self, request, pk, step):
        """Get objects."""
        revision = get_object_or_404(_revision_queryset(request.user), pk=pk)
        return revision, get_object_or_404(revision.steps, key=step)

    def patch(self, request, pk, step):
        """Handle PATCH requests."""
        _revision, row = self.get_objects(request, pk, step)
        serializer = EditDraftStepSerializer(row, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        expected = values.pop('expected_content_version')
        try:
            row = edit_draft_step(row, request.user, expected, **values)
        except (ProcedureCommandError, PermissionDenied) as exc:
            return _error_response(exc)
        except ScopeError as exc:
            raise Http404 from exc
        return Response(ProcedureStepSerializer(row).data)

    def delete(self, request, pk, step):
        """Handle DELETE requests."""
        _revision, row = self.get_objects(request, pk, step)
        serializer = RequestReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            edit_draft_step(
                row,
                request.user,
                serializer.validated_data['expected_content_version'],
                delete=True,
            )
        except (ProcedureCommandError, PermissionDenied) as exc:
            return _error_response(exc)
        except ScopeError as exc:
            raise Http404 from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProcedureStepReorder(ProcedureEnabledMixin, APIView):
    """Procedure step reorder."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        """Handle POST requests."""
        revision = get_object_or_404(_revision_queryset(request.user), pk=pk)
        serializer = ReorderStepsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            rows = reorder_draft_steps(
                revision, request.user, **serializer.validated_data
            )
        except (ProcedureCommandError, PermissionDenied) as exc:
            return _error_response(exc)
        except ScopeError as exc:
            raise Http404 from exc
        return Response(ProcedureStepSerializer(rows, many=True).data)


class ProcedureResourceList(ProcedureEnabledMixin, APIView):
    """Procedure resource list."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        """Handle GET requests."""
        revision = get_object_or_404(_revision_queryset(request.user), pk=pk)
        rows = revision.resource_requirements.order_by('sequence')
        return Response(ProcedureResourceRequirementSerializer(rows, many=True).data)

    def post(self, request, pk):
        """Handle POST requests."""
        revision = get_object_or_404(_revision_queryset(request.user), pk=pk)
        serializer = EditDraftResourceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        expected = values.pop('expected_content_version')
        try:
            row = create_draft_resource(revision, request.user, expected, **values)
        except (ProcedureCommandError, PermissionDenied) as exc:
            return _error_response(exc)
        except ScopeError as exc:
            raise Http404 from exc
        return Response(
            ProcedureResourceRequirementSerializer(row).data,
            status=status.HTTP_201_CREATED,
        )


class ProcedureResourceDetail(ProcedureEnabledMixin, APIView):
    """Procedure resource detail."""

    permission_classes = [IsAuthenticated]

    def get_objects(self, request, pk, line):
        """Get objects."""
        revision = get_object_or_404(_revision_queryset(request.user), pk=pk)
        return revision, get_object_or_404(revision.resource_requirements, key=line)

    def patch(self, request, pk, line):
        """Handle PATCH requests."""
        _revision, row = self.get_objects(request, pk, line)
        serializer = EditDraftResourceSerializer(row, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        expected = values.pop('expected_content_version')
        try:
            row = edit_draft_resource(row, request.user, expected, **values)
        except (ProcedureCommandError, PermissionDenied) as exc:
            return _error_response(exc)
        except ScopeError as exc:
            raise Http404 from exc
        return Response(ProcedureResourceRequirementSerializer(row).data)

    def delete(self, request, pk, line):
        """Handle DELETE requests."""
        _revision, row = self.get_objects(request, pk, line)
        serializer = RequestReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            edit_draft_resource(
                row,
                request.user,
                serializer.validated_data['expected_content_version'],
                delete=True,
            )
        except (ProcedureCommandError, PermissionDenied) as exc:
            return _error_response(exc)
        except ScopeError as exc:
            raise Http404 from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProcedureBlockers(ProcedureEnabledMixin, APIView):
    """Procedure blockers."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        """Handle GET requests."""
        revision = get_object_or_404(_revision_queryset(request.user), pk=pk)
        return Response(
            ProcedureBlockerSerializer(
                review_blockers(revision, reviewer=request.user), many=True
            ).data
        )


class ProcedureRequestReview(ProcedureEnabledMixin, APIView):
    """Procedure request review."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        """Handle POST requests."""
        revision = get_object_or_404(_revision_queryset(request.user), pk=pk)
        serializer = RequestReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            approval = request_review(
                revision, request.user, **serializer.validated_data
            )
        except (ProcedureCommandError, PermissionDenied) as exc:
            return _error_response(exc)
        except ScopeError as exc:
            raise Http404 from exc
        return Response(_approval_data(approval), status=status.HTTP_201_CREATED)


class ProcedurePublish(ProcedureEnabledMixin, APIView):
    """Return the approval entry point without directly publishing content."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        """Handle POST requests."""
        revision = get_object_or_404(_revision_queryset(request.user), pk=pk)
        serializer = PublishProcedureSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        approvals = Approval.objects.filter(
            action_type=ActionType.PROCEDURE_PUBLISH,
            payload__revision_id=revision.pk,
            payload__content_hash=revision.content_hash,
        )
        approval_id = serializer.validated_data.get('approval_id')
        if approval_id:
            approvals = approvals.filter(pk=approval_id)
        approval = approvals.order_by('-created_at').first()
        if approval is None:
            return Response(
                {'detail': 'No current publication approval exists'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(_approval_data(approval))


class ProcedureArchive(ProcedureEnabledMixin, APIView):
    """Procedure archive."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        """Handle POST requests."""
        revision = get_object_or_404(_revision_queryset(request.user), pk=pk)
        serializer = ArchiveProcedureRevisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:  # noqa: PLW0717 - established archive guard block
            require_permission(request.user, PUBLISH_PROCEDURE)
            if revision.status not in {
                ProcedureRevisionStatus.PUBLISHED,
                ProcedureRevisionStatus.SUPERSEDED,
            }:
                raise ProcedureCommandError(
                    'Only published or superseded revisions can be archived'
                )
            revision.status = ProcedureRevisionStatus.ARCHIVED
            revision.save(update_fields=['status'])
            if revision.procedure.current_revision_id == revision.pk:
                revision.procedure.current_revision = None
                revision.procedure.save(
                    update_fields=['current_revision', 'updated_at']
                )
        except (ProcedureCommandError, PermissionDenied) as exc:
            return _error_response(exc)
        return Response(ProcedureRevisionSerializer(revision).data)
