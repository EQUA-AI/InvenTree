"""Unwired readers for attachments directly owned by scoped Work.

Callers must bind the actor to a trusted authenticated request and obtain the
source environment from server configuration, never remote-supplied identifiers.
"""

import hashlib
from dataclasses import dataclass, field

from assets.integration_contracts import AttachmentSourceIdentity
from assets.integration_sources import (
    MAX_MACHINE_ATTACHMENT_CONTENT_BYTES as MAX_MACHINE_ATTACHMENT_CONTENT_BYTES,
)
from assets.integration_sources import (
    MAX_MACHINE_ATTACHMENT_CONTENT_READS as MAX_MACHINE_ATTACHMENT_CONTENT_READS,
)
from assets.integration_sources import (
    AttachmentContentAbsentError as AttachmentContentAbsentError,
)
from assets.integration_sources import (
    AttachmentContentLimitError as AttachmentContentLimitError,
)
from assets.integration_sources import AttachmentContentUnavailableError
from tasks.integration_sources import WorkSourceIdentity


@dataclass(frozen=True)
class WorkAttachmentSourceRecord:
    """An authorized Work attachment identity and its Work owner only."""

    identity: AttachmentSourceIdentity
    work: WorkSourceIdentity
    schema_version: str = field(default='aimms.work-attachment.basic.v1', init=False)


@dataclass(frozen=True)
class WorkAttachmentContentRecord:
    """Exact bounded bytes from an authorized direct Work attachment."""

    identity: AttachmentSourceIdentity
    work: WorkSourceIdentity
    content: bytes = field(repr=False)
    content_sha256: str
    schema_version: str = field(default='aimms.work-attachment.content.v1', init=False)


def read_work_attachment(actor, *, environment, attachment_pk):
    """Observe the exact authorized owner twice without a discarded Work DTO.

    Each observation retains fresh native account/role/model permission,
    direct attachment proof and current scoped Work proof in the owner-edge
    query. Construct the private identity between observations so a constructor
    cannot outlive a revocation/relink. No result is a reusable capability or
    an atomic DB/storage snapshot; the byte reader and later uses reauthorize.
    """
    from django.contrib.auth import get_user_model

    from ai.core.integrations.retrieval_authority import fresh_role
    from common.models import Attachment
    from common.validators import attachment_model_types
    from tasks.integration_sources import _integration_scoped_work_orders
    from tasks.models import WorkOrder

    identity = AttachmentSourceIdentity(environment=environment, pk=attachment_pk)
    if actor is None or not getattr(actor, 'is_authenticated', False):
        return None
    actor_pk = getattr(actor, 'pk', None)
    if type(actor_pk) is not int or actor_pk < 1:
        return None

    def observe_owner():
        user = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
        if (
            user is None
            or not fresh_role(user, 'work_order')
            or not WorkOrder.check_related_permission('view', user)
        ):
            return None
        matching_types = [
            model
            for model in attachment_model_types()
            if model.__name__.lower() == 'workorder'
        ]
        if matching_types != [WorkOrder]:
            return None
        row = (
            Attachment.objects
            .filter(pk=attachment_pk, model_type='workorder')
            .values('model_id')
            .first()
        )
        if row is None or type(row['model_id']) is not int or row['model_id'] < 1:
            return None
        from assets.integration_attribution import (
            attributed_attachments,
            authority_enabled,
        )

        if (
            authority_enabled()
            and not attributed_attachments(
                Attachment.objects.filter(pk=attachment_pk),
                actor=user,
                environment=environment,
                source_pk=attachment_pk,
            ).exists()
        ):
            return None
        # Attachment proof can read storage. Fresh native checks follow it,
        # then the actual edge intersects current Work scope and source proof.
        user = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
        if (
            user is None
            or not fresh_role(user, 'work_order')
            or not WorkOrder.check_related_permission('view', user)
        ):
            return None
        if not Attachment.objects.filter(
            pk=attachment_pk,
            model_type='workorder',
            model_id=row['model_id'],
            model_id__in=_integration_scoped_work_orders(
                user, environment=environment, work_pk=row['model_id']
            ),
        ).exists():
            return None
        return row['model_id']

    owner_pk = observe_owner()
    if owner_pk is None:
        return None
    try:
        projected = WorkAttachmentSourceRecord(
            identity=identity,
            work=WorkSourceIdentity(environment=environment, pk=owner_pk),
        )
    except (TypeError, ValueError):
        # Preserve a fresh fault-path check as well as the successful return.
        observe_owner()
        return None
    if observe_owner() != owner_pk:
        return None
    if (
        type(projected) is not WorkAttachmentSourceRecord
        or type(projected.identity) is not AttachmentSourceIdentity
        or type(projected.work) is not WorkSourceIdentity
        or type(projected.identity.environment) is not str
        or projected.identity.environment != environment
        or type(projected.identity.pk) is not int
        or projected.identity.pk != attachment_pk
        or type(projected.work.environment) is not str
        or projected.work.environment != environment
        or type(projected.work.pk) is not int
        or projected.work.pk != owner_pk
        or type(projected.schema_version) is not str
        or projected.schema_version != 'aimms.work-attachment.basic.v1'
    ):
        return None
    return projected


def read_work_attachment_content(actor, *, environment, attachment_pk, max_bytes):
    """Return captured native bytes after the final current-owner check.

    The private synchronous assembly retains entry/pre-storage/return checks.
    Caller-visible DTOs cannot replace bytes or bind another identity. Every
    later use needs current authority; this is not a streaming or storage lease.
    """
    from assets.integration_content_read import (
        _original_assembly,
        _OriginalWithheldError,
        _projection_matches,
    )

    record_type = WorkAttachmentContentRecord
    try:
        with _original_assembly(
            actor,
            environment=environment,
            attachment_pk=attachment_pk,
            max_bytes=max_bytes,
            owner_reader=read_work_attachment,
            model_type='workorder',
            owner_field='work',
        ) as assembly:
            content = assembly.read_bytes()
            owner = (
                type(assembly.owner),
                assembly.owner.environment,
                assembly.owner.pk,
            )
            try:
                projected = record_type(
                    identity=assembly.identity,
                    work=assembly.owner,
                    content=content,
                    content_sha256=hashlib.sha256(content).hexdigest(),
                )
            except (TypeError, ValueError):
                raise AttachmentContentUnavailableError(
                    'Stored content is unavailable'
                ) from None
            if not _projection_matches(
                projected,
                record_type=record_type,
                identity=(environment, attachment_pk),
                owner=owner,
                owner_field='work',
                content=content,
            ):
                raise AttachmentContentUnavailableError('Stored content is unavailable')
        # The final native check may itself call fallible source code. Recheck
        # all private values afterwards without any new external callback.
        if not _projection_matches(
            projected,
            record_type=record_type,
            identity=(environment, attachment_pk),
            owner=owner,
            owner_field='work',
            content=content,
        ):
            raise AttachmentContentUnavailableError('Stored content is unavailable')
        return projected
    except _OriginalWithheldError:
        return None
