"""Bounded original downloads using the existing default-off source authority."""

from django.apps import apps
from django.conf import settings
from django.http import HttpResponse

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound, ParseError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView


def _enabled():
    return getattr(
        settings, 'AIMMS_INTEGRATION_AUTHORITY_ENABLED', False
    ) is True and apps.is_installed('integration_state')


def _namespace():
    from integration_state.models import SourceNamespace

    rows = list(
        SourceNamespace.objects
        .filter(active=True)
        .order_by('pk')
        .values_list('pk', 'epoch', 'environment')[:2]
    )
    # Neither the caller nor the first row in an ambiguous registry selects
    # source authority. Configuration must identify exactly one active source.
    return rows[0] if len(rows) == 1 else None


class IntegrationOriginalDownload(APIView):
    """Return exact small originals, independently of metadata/list permission."""

    permission_classes = (IsAuthenticated,)
    http_method_names = ('get', 'options')

    def initial(self, request, *args, **kwargs):
        """Stay absent before authentication or state work when not enrolled."""
        if not _enabled():
            raise NotFound('Source content unavailable')
        return super().initial(request, *args, **kwargs)

    @extend_schema(
        operation_id='integration_original_download',
        responses={
            (200, 'application/octet-stream'): OpenApiTypes.BINARY,
            400: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
        },
    )
    def get(self, request, pk):
        """Reobserve the same authorized bytes before constructing a response.

        A path PK is selection, never permission. Native actor/owner/role and
        current explicit grants plus exact future proof remain mandatory. This
        bounded response is not a streaming lease or an atomic revocation claim.
        """
        from tasks.integration_s3_documents import (
            DocumentMetadataUnavailableError,
            read_machine_document_content,
            read_work_document_content,
        )

        from assets.integration_authority import integration_actor_from_request
        from assets.integration_sources import (
            MAX_MACHINE_ATTACHMENT_CONTENT_BYTES,
            AttachmentContentAbsentError,
            AttachmentContentLimitError,
            AttachmentContentUnavailableError,
        )
        from common.models import Attachment

        if request.query_params or request.body:
            raise ParseError('Source selection parameters are unsupported')
        namespace = _namespace()
        actor = integration_actor_from_request(request, required_role='work_order')
        if namespace is None or actor is None:
            raise NotFound('Source content unavailable')
        kind = (
            Attachment.objects
            .filter(pk=pk)
            .values_list('model_type', flat=True)
            .first()
        )
        reader = {
            'workorder': read_work_document_content,
            'assetmachine': read_machine_document_content,
        }.get(kind)
        if reader is None:
            raise NotFound('Source content unavailable')

        def read():
            current = integration_actor_from_request(
                request, required_role='work_order'
            )
            if current is None or current.pk != actor.pk or not _enabled():
                return None
            return reader(
                current,
                environment=namespace[2],
                attachment_pk=pk,
                max_bytes=MAX_MACHINE_ATTACHMENT_CONTENT_BYTES,
            )

        try:
            observed = read()
            if observed is None:
                raise NotFound('Source content unavailable')
            response = HttpResponse(
                observed.content, content_type='application/octet-stream'
            )
            response['Content-Disposition'] = 'attachment; filename="original.bin"'
            response['Cache-Control'] = 'private, no-store'
            response['Pragma'] = 'no-cache'
            response['Vary'] = 'Cookie, Authorization'
            response['X-Content-Type-Options'] = 'nosniff'
            if read() != observed or _namespace() != namespace or not _enabled():
                raise NotFound('Source content unavailable')
        except (
            AttachmentContentAbsentError,
            AttachmentContentLimitError,
            AttachmentContentUnavailableError,
            DocumentMetadataUnavailableError,
            TypeError,
            ValueError,
        ):
            raise NotFound('Source content unavailable') from None
        return response
