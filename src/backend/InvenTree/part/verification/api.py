"""Scope-safe resources and explicit commands for part verification.

Every endpoint authenticates; querysets are scope-filtered before lookup,
filter, search, or count; generic state PATCH is rejected by construction
(there is no writable resource serializer); and every command failure returns
the stable error envelope of spec section 15.3.
"""

import uuid

from django.conf import settings
from django.db.models import Q
from django.http import Http404
from django.urls import include, path

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from part.verification import services
from part.verification.errors import VerificationCommandError
from part.verification.requirements import validate_context
from part.verification.schema import PartVerificationState
from part.verification.scope import VerificationScopeError, scope_for_actor
from part.verification.serializers import (
    AttachEvidenceSerializer,
    CancelCommandSerializer,
    CreateSessionSerializer,
    DecideEvidenceSerializer,
    PartCandidateEvaluationSerializer,
    PartVerificationDecisionSerializer,
    PartVerificationEventSerializer,
    PartVerificationEvidenceSerializer,
    PartVerificationRequirementSerializer,
    PartVerificationSessionSerializer,
    PartVerificationUseSerializer,
    ReasonedCommandSerializer,
    ReasonedRevisionCommandSerializer,
    RevisionCommandSerializer,
)
from part.verification_models import PartVerificationDecision, PartVerificationSession


class RPFEnabledMixin:
    """Hide verification resources entirely while the feature flag is off."""

    def dispatch(self, request, *args, **kwargs):
        """Return a normal 404 while the additive API is disabled."""
        if not getattr(settings, 'AIMMS_RPF_ENABLED', False):
            raise Http404
        return super().dispatch(request, *args, **kwargs)


def _scoped_sessions(user):
    """Return sessions visible to the actor's resolved scopes, fail closed.

    Scope filtering precedes every lookup, count, and search (RPF-ADR-010);
    an unresolved scope yields an empty queryset, never an error that would
    disclose existence.
    """
    try:
        scopes = scope_for_actor(user)
    except VerificationScopeError:
        return PartVerificationSession.objects.none()

    condition = Q(pk__in=[])
    for scope in scopes:
        entry = (
            Q(scope_customer_id=scope.customer_id)
            if scope.customer_id is not None
            else Q(scope_customer__isnull=True)
        )
        entry &= Q(scope_site_key=scope.site_key or '')
        condition |= entry

    return PartVerificationSession.objects.filter(condition).select_related(
        'policy', 'requested_part', 'current_decision'
    )


def _scoped_session_or_404(request, pk):
    """Resolve one session under actor scope or return a scope-safe 404."""
    return get_object_or_404(_scoped_sessions(request.user), pk=pk)


def _error_response(exc: VerificationCommandError, correlation_id, current_revision):
    """Build the stable command error envelope."""
    return Response(
        {
            'code': exc.code,
            'detail': str(exc),
            'field_errors': {},
            'blockers': exc.blockers,
            'current_revision': current_revision,
            'correlation_id': str(correlation_id),
            'retryable': exc.retryable,
        },
        status=exc.http_status,
    )


def _current_revision(session_pk):
    """Re-read the session revision after a transactional rollback."""
    return (
        PartVerificationSession.objects
        .filter(pk=session_pk)
        .values_list('revision', flat=True)
        .first()
    )


class SessionList(RPFEnabledMixin, APIView):
    """List scoped sessions or create a new one."""

    permission_classes = [IsAuthenticated]
    serializer_class = CreateSessionSerializer

    @extend_schema(operation_id='part_verification_sessions_list')
    def get(self, request):
        """Return scoped sessions with simple attribute filters."""
        queryset = _scoped_sessions(request.user).order_by('-pk')

        for field in ('state', 'purpose'):
            value = request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value})
        for field in ('requested_part', 'machine', 'bom_item'):
            value = request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{f'{field}_id': value})

        return Response(
            PartVerificationSessionSerializer(queryset[:100], many=True).data
        )

    def post(self, request):
        """Create one scoped verification session."""
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        correlation_id = uuid.uuid4()
        try:
            session = services.create_session(
                actor=request.user,
                correlation_id=str(correlation_id),
                **serializer.validated_data,
            )
        except VerificationCommandError as exc:
            return _error_response(exc, correlation_id, None)
        return Response(
            PartVerificationSessionSerializer(session).data,
            status=status.HTTP_201_CREATED,
        )


class SessionDetail(RPFEnabledMixin, APIView):
    """Session detail; no generic state PATCH exists."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        """Return one scoped session."""
        session = _scoped_session_or_404(request, pk)
        return Response(PartVerificationSessionSerializer(session).data)


class SessionReadiness(RPFEnabledMixin, APIView):
    """Collection/evaluation/review readiness with stable blockers."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        """Return current blockers without mutating anything."""
        session = _scoped_session_or_404(request, pk)

        blockers = [blocker.as_dict() for blocker in validate_context(session)]
        for row in session.requirements.filter(hard_constraint=True).exclude(
            blocker_code=''
        ):
            blockers.append({
                'code': row.blocker_code,
                'attribute': row.key,
                'message': 'Required fact is missing, invalid, or conflicting',
                'remediation': 'Attach or accept authoritative evidence',
            })

        ready_for = 'human_review'
        if session.state == PartVerificationState.COLLECTING:
            ready_for = 'evaluation' if not blockers else 'collection'

        return Response({
            'ready': not blockers,
            'ready_for': ready_for,
            'state': session.state,
            'revision': session.revision,
            'policy': {'key': session.policy.key, 'version': session.policy.version},
            'universe_complete': session.universe_complete,
            'blockers': blockers,
            'warnings': [],
            'correlation_id': str(uuid.uuid4()),
        })


class SessionObservationPreview(RPFEnabledMixin, APIView):
    """Unlocked baseline/current difference preview; carries no authority."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        """Return the preview comparison for the current decision."""
        session = _scoped_session_or_404(request, pk)
        return Response(services.current_observation_preview(session))


class SessionCommandView(RPFEnabledMixin, APIView):
    """Shared adapter from a validated command intent to a service call."""

    permission_classes = [IsAuthenticated]
    serializer_class = None

    def invoke(self, request, session, data, **kwargs):
        """Run the domain service; subclasses return the response payload."""
        raise NotImplementedError

    def post(self, request, pk, **kwargs):
        """Validate intent, resolve scope, and invoke the service."""
        session = _scoped_session_or_404(request, pk)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        correlation_id = uuid.uuid4()
        try:
            payload = self.invoke(
                request, session, dict(serializer.validated_data), **kwargs
            )
        except VerificationCommandError as exc:
            return _error_response(exc, correlation_id, _current_revision(session.pk))
        return Response(payload)


class SessionEvaluate(SessionCommandView):
    """Deterministic requirement construction and candidate evaluation."""

    serializer_class = RevisionCommandSerializer

    def invoke(self, request, session, data):
        """Invoke the evaluate command."""
        return services.evaluate_session(
            session_id=session.pk, actor=request.user, **data
        )


class SessionReevaluate(SessionCommandView):
    """Reopen a stale session as a new revision."""

    serializer_class = RevisionCommandSerializer

    def invoke(self, request, session, data):
        """Invoke the reevaluate command."""
        return services.reevaluate_session(
            session_id=session.pk, actor=request.user, **data
        )


class SessionCancel(SessionCommandView):
    """Controlled session cancellation."""

    serializer_class = CancelCommandSerializer

    def invoke(self, request, session, data):
        """Invoke the cancel command."""
        result = services.cancel_session(
            session_id=session.pk, actor=request.user, **data
        )
        return PartVerificationSessionSerializer(result).data


class SessionInvalidate(SessionCommandView):
    """Authorized invalidation of a decided session."""

    serializer_class = ReasonedCommandSerializer

    def invoke(self, request, session, data):
        """Invoke the invalidate command."""
        result = services.invalidate_session(
            session_id=session.pk, actor=request.user, **data
        )
        return PartVerificationSessionSerializer(result).data


class SessionNoSafeMatch(SessionCommandView):
    """Record a complete safe abstention."""

    serializer_class = ReasonedRevisionCommandSerializer

    def invoke(self, request, session, data):
        """Invoke the no-safe-match command."""
        decision = services.mark_no_safe_match(
            session_id=session.pk, actor=request.user, **data
        )
        return PartVerificationDecisionSerializer(decision).data


class SessionRequirementList(RPFEnabledMixin, APIView):
    """Ordered requirements and their blocker states."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        """Return the current typed requirements."""
        session = _scoped_session_or_404(request, pk)
        rows = session.requirements.order_by('key')
        return Response(PartVerificationRequirementSerializer(rows, many=True).data)


class SessionEvidenceList(RPFEnabledMixin, APIView):
    """List evidence or attach a proposed item."""

    permission_classes = [IsAuthenticated]
    serializer_class = AttachEvidenceSerializer

    def get(self, request, pk):
        """Return the session's evidence items."""
        session = _scoped_session_or_404(request, pk)
        rows = session.evidence_items.order_by('pk')
        return Response(PartVerificationEvidenceSerializer(rows, many=True).data)

    def post(self, request, pk):
        """Attach one proposed evidence item."""
        session = _scoped_session_or_404(request, pk)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        correlation_id = uuid.uuid4()
        try:
            evidence = services.attach_evidence(
                session_id=session.pk,
                actor=request.user,
                correlation_id=str(correlation_id),
                **serializer.validated_data,
            )
        except VerificationCommandError as exc:
            return _error_response(exc, correlation_id, _current_revision(session.pk))
        return Response(
            PartVerificationEvidenceSerializer(evidence).data,
            status=status.HTTP_201_CREATED,
        )


class SessionEvidenceDecide(SessionCommandView):
    """Accept or reject one proposed evidence item."""

    serializer_class = DecideEvidenceSerializer

    def invoke(self, request, session, data, evidence):
        """Invoke the evidence decision command."""
        row = services.decide_evidence(
            session_id=session.pk, evidence_id=evidence, actor=request.user, **data
        )
        return PartVerificationEvidenceSerializer(row).data


class SessionCandidateList(RPFEnabledMixin, APIView):
    """Paginated candidate summaries in backend comparison order."""

    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id='part_verification_sessions_candidates_list')
    def get(self, request, pk):
        """Return candidate evaluations for the current revision."""
        session = _scoped_session_or_404(request, pk)
        rows = session.candidate_evaluations.filter(
            session_revision=session.revision
        ).select_related('candidate')

        eligible = request.query_params.get('eligible')
        if eligible is not None:
            rows = rows.filter(eligible=eligible in ('1', 'true', 'True'))

        # Survivors first in rank order, then exclusions by candidate pk;
        # an excluded candidate can never sort above a survivor.
        rows = sorted(
            rows, key=lambda row: (0, row.rank) if row.rank else (1, row.candidate_id)
        )
        return Response(PartCandidateEvaluationSerializer(rows[:200], many=True).data)


class SessionCandidateDetail(RPFEnabledMixin, APIView):
    """Full comparison and factor detail for one candidate."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk, evaluation):
        """Return one candidate evaluation."""
        session = _scoped_session_or_404(request, pk)
        row = get_object_or_404(session.candidate_evaluations, pk=evaluation)
        return Response(PartCandidateEvaluationSerializer(row).data)


class SessionCandidateReject(SessionCommandView):
    """Human rejection of one candidate with a reason."""

    serializer_class = ReasonedRevisionCommandSerializer

    def invoke(self, request, session, data, evaluation):
        """Invoke the reject-candidate command."""
        row = services.reject_candidate(
            session_id=session.pk, evaluation_id=evaluation, actor=request.user, **data
        )
        return PartCandidateEvaluationSerializer(row).data


class SessionCandidateConfirm(SessionCommandView):
    """Locked human confirmation of one eligible candidate."""

    serializer_class = ReasonedRevisionCommandSerializer

    def invoke(self, request, session, data, evaluation):
        """Invoke the confirm command."""
        decision = services.confirm_candidate(
            session_id=session.pk, evaluation_id=evaluation, actor=request.user, **data
        )
        return PartVerificationDecisionSerializer(decision).data


class SessionDecisionList(RPFEnabledMixin, APIView):
    """Immutable decision history for a session."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        """Return the session's decisions, newest first."""
        session = _scoped_session_or_404(request, pk)
        rows = session.decisions.order_by('-pk')
        return Response(PartVerificationDecisionSerializer(rows, many=True).data)


class SessionEventList(RPFEnabledMixin, APIView):
    """Append-only event history for a session."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        """Return the session's events in order."""
        session = _scoped_session_or_404(request, pk)
        rows = session.events.order_by('pk')
        return Response(PartVerificationEventSerializer(rows, many=True).data)


def _scoped_decision_or_404(request, did):
    """Resolve one decision whose session is within actor scope."""
    sessions = _scoped_sessions(request.user).values_list('pk', flat=True)
    return get_object_or_404(
        PartVerificationDecision.objects.filter(session_id__in=sessions), pk=did
    )


class DecisionDetail(RPFEnabledMixin, APIView):
    """Exact decision snapshot metadata and current status."""

    permission_classes = [IsAuthenticated]

    def get(self, request, did):
        """Return one scoped decision."""
        decision = _scoped_decision_or_404(request, did)
        return Response(PartVerificationDecisionSerializer(decision).data)


class DecisionUseList(RPFEnabledMixin, APIView):
    """Effect bindings recorded against one decision."""

    permission_classes = [IsAuthenticated]

    def get(self, request, did):
        """Return the decision's use rows."""
        decision = _scoped_decision_or_404(request, did)
        return Response(
            PartVerificationUseSerializer(decision.uses.all(), many=True).data
        )


session_detail_urls = [
    path('evaluate/', SessionEvaluate.as_view(), name='api-rpf-session-evaluate'),
    path('reevaluate/', SessionReevaluate.as_view(), name='api-rpf-session-reevaluate'),
    path('cancel/', SessionCancel.as_view(), name='api-rpf-session-cancel'),
    path('invalidate/', SessionInvalidate.as_view(), name='api-rpf-session-invalidate'),
    path('readiness/', SessionReadiness.as_view(), name='api-rpf-session-readiness'),
    path(
        'current-observation/',
        SessionObservationPreview.as_view(),
        name='api-rpf-session-observation',
    ),
    path(
        'requirements/',
        SessionRequirementList.as_view(),
        name='api-rpf-session-requirements',
    ),
    path(
        'evidence/<int:evidence>/decide/',
        SessionEvidenceDecide.as_view(),
        name='api-rpf-evidence-decide',
    ),
    path('evidence/', SessionEvidenceList.as_view(), name='api-rpf-session-evidence'),
    path(
        'candidates/<int:evaluation>/reject/',
        SessionCandidateReject.as_view(),
        name='api-rpf-candidate-reject',
    ),
    path(
        'candidates/<int:evaluation>/confirm/',
        SessionCandidateConfirm.as_view(),
        name='api-rpf-candidate-confirm',
    ),
    path(
        'candidates/<int:evaluation>/',
        SessionCandidateDetail.as_view(),
        name='api-rpf-candidate-detail',
    ),
    path('candidates/', SessionCandidateList.as_view(), name='api-rpf-candidates'),
    path('no-safe-match/', SessionNoSafeMatch.as_view(), name='api-rpf-no-safe-match'),
    path('decisions/', SessionDecisionList.as_view(), name='api-rpf-session-decisions'),
    path('events/', SessionEventList.as_view(), name='api-rpf-session-events'),
    path('', SessionDetail.as_view(), name='api-rpf-session-detail'),
]

verification_api_urls = [
    path('sessions/<int:pk>/', include(session_detail_urls)),
    path('sessions/', SessionList.as_view(), name='api-rpf-session-list'),
    path(
        'decisions/<int:did>/uses/',
        DecisionUseList.as_view(),
        name='api-rpf-decision-uses',
    ),
    path(
        'decisions/<int:did>/', DecisionDetail.as_view(), name='api-rpf-decision-detail'
    ),
]
