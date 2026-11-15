"""Default-off native request/review actions; no enrollment or erasure endpoint."""

from django.apps import apps
from django.conf import settings
from django.urls import path

from rest_framework.authentication import BasicAuthentication, SessionAuthentication
from rest_framework.exceptions import NotFound, ParseError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


def _body(request, fields):
    if len(request.body) > 65536:
        raise ParseError('Invalid authority request')
    value = request.data
    if type(value) is not dict or set(value) != set(fields):
        raise ParseError('Invalid authority request')
    return value


class NativeAuthorityAction(APIView):
    """Keep native authentication/CSRF and service-owned client/action checks."""

    authentication_classes = (BasicAuthentication, SessionAuthentication)
    permission_classes = (IsAuthenticated,)
    parser_classes = (JSONParser,)

    def initial(self, request, *args, **kwargs):
        """Do no state/authentication work while optional enrollment is off."""
        if getattr(
            settings, 'AIMMS_INTEGRATION_AUTHORITY_ENABLED', False
        ) is not True or not apps.is_installed('integration_state'):
            raise NotFound('Authority request unavailable')
        return super().initial(request, *args, **kwargs)

    def post(self, request, **kwargs):
        """Do not project source/identity/storage exception details to a caller."""
        from integration_state.native_authority import NativeAuthorityError

        try:
            return self.act(request, **kwargs)
        except (NativeAuthorityError, PermissionError):
            raise NotFound('Authority request unavailable') from None
        except (TypeError, ValueError):
            raise ParseError('Invalid authority request') from None

    def act(self, request, **kwargs):
        """Implement one explicit action without general-purpose dispatch input."""
        raise NotImplementedError


class NativeAuthorityCreate(NativeAuthorityAction):
    """Submit a proposed attribution or an explicitly incomplete readiness scope."""

    def act(self, request):
        """The active source namespace/environment/epoch are server-selected."""
        from integration_state import native_authority as service
        from integration_state.models import SourceNamespace
        from integration_state.s7_contracts import SourceAspectIdentity

        if len(request.body) > 65536 or type(request.data) is not dict:
            raise ParseError('Invalid authority request')
        namespace = SourceNamespace.objects.filter(active=True).first()
        if namespace is None:
            raise service.NativeAuthorityError()
        purpose = request.data.get('purpose')
        if purpose == 'attribution':
            value = _body(
                request,
                (
                    'purpose',
                    'kind',
                    'source_pk',
                    'client_ids',
                    'evidence_reference',
                    'request_key',
                ),
            )
            row = service.request_attribution(
                request,
                namespace_id=namespace.pk,
                **{k: v for k, v in value.items() if k != 'purpose'},
            )
        elif purpose == 'deletion_readiness':
            value = _body(
                request,
                (
                    'purpose',
                    'client_pk',
                    'client_request_reference',
                    'request_key',
                    'scope',
                ),
            )
            scope = value['scope']
            if (
                type(scope) is not list
                or not 1 <= len(scope) <= 1000
                or any(
                    type(s) is not dict
                    or set(s) != {'entity_kind', 'aspect', 'source_id'}
                    for s in scope
                )
            ):
                raise ParseError('Invalid authority request')
            row = service.request_deletion_readiness(
                request,
                namespace_id=namespace.pk,
                client_pk=value['client_pk'],
                client_request_reference=value['client_request_reference'],
                request_key=value['request_key'],
                scope=tuple(
                    SourceAspectIdentity(
                        environment=namespace.environment, epoch=namespace.epoch, **s
                    )
                    for s in scope
                ),
            )
        else:
            raise ParseError('Invalid authority request')
        return Response(
            {
                'request_pk': row.pk,
                'request_digest': row.binding_digest,
                'purpose': row.purpose,
            },
            status=201,
        )


class NativeAuthorityInspect(NativeAuthorityAction):
    """Give an eligible different reviewer the exact current bounded impact."""

    def act(self, request, pk):
        """POST keeps this action under the same native write-authentication policy."""
        from integration_state.native_authority import inspect_authority_request

        _body(request, ())
        return Response(inspect_authority_request(request, request_pk=pk))


class NativeAuthorityReview(NativeAuthorityAction):
    """Bind the different reviewer's decision to the exact inspected revision."""

    def act(self, request, pk):
        """Approving readiness authorizes no deletion or retention exception."""
        from integration_state.native_authority import decide_authority_request

        value = _body(
            request, ('expected_request_digest', 'expected_impact_digest', 'approved')
        )
        decision = decide_authority_request(request, request_pk=pk, **value)
        return Response({
            'request_pk': pk,
            'approved_review': decision.approved,
            'erasure_authorized': False,
        })


class NativeAuthorityWithdraw(NativeAuthorityAction):
    """Withdraw a source claim while retaining its request and review records."""

    def act(self, request, pk):
        """A fresh native administrator needs every declared client grant."""
        from integration_state.native_authority import withdraw_authority_request

        value = _body(request, ('expected_request_digest', 'reason_reference'))
        withdraw_authority_request(request, request_pk=pk, **value)
        return Response({
            'request_pk': pk,
            'review_status': 'withdrawn',
            'erasure_authorized': False,
        })


authority_urls = [
    path('', NativeAuthorityCreate.as_view(), name='integration-authority-create'),
    path(
        '<int:pk>/inspect/',
        NativeAuthorityInspect.as_view(),
        name='integration-authority-inspect',
    ),
    path(
        '<int:pk>/review/',
        NativeAuthorityReview.as_view(),
        name='integration-authority-review',
    ),
    path(
        '<int:pk>/withdraw/',
        NativeAuthorityWithdraw.as_view(),
        name='integration-authority-withdraw',
    ),
]
