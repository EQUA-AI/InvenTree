"""Native, read-only sources for the optional integration boundary.

This module is not wired to any endpoint or provider. Callers must obtain the
authenticated actor from a trusted request context and the source environment
from server configuration, never from remote user-supplied identifiers. A
reloaded user by primary key is not proof of the caller's identity.
"""

from .integration_contracts import (
    AttachmentSourceIdentity,
    ClientDetailsSourceRecord,
    ClientSourceIdentity,
    ClientSourceRecord,
    ClientStructureSourceRecord,
    LocationDetailsSourceRecord,
    LocationPropertiesSourceRecord,
    LocationSourceIdentity,
    LocationSourceRecord,
    MachineAttachmentContentRecord,
    MachineAttachmentSourceRecord,
    MachineBarcodeSourceRecord,
    MachineDetailsSourceRecord,
    MachineProfileSourceRecord,
    MachineRegistrationSourceRecord,
    MachineSourceIdentity,
    MachineSourceRecord,
    MaintenanceSourceIdentity,
    MaintenanceSourceRecord,
)

MAX_MACHINE_ATTACHMENT_CONTENT_BYTES = 1024 * 1024
MAX_MACHINE_ATTACHMENT_CONTENT_READS = 1024


class AttachmentContentAbsentError(Exception):
    """The authorized attachment has no stored content."""


class AttachmentContentUnavailableError(Exception):
    """The authorized attachment's stored content cannot be read."""


class AttachmentContentLimitError(Exception):
    """The stored content exceeds this operation's explicit bound."""


def _authorized_client(actor, identity):
    """Resolve the current client with native admin role and explicit grant."""
    from django.contrib.auth import get_user_model

    from ai.core.integrations.retrieval_authority import fresh_role
    from assets.integration_authority import explicitly_granted_client_ids
    from assets.models import Client

    if actor is None or not getattr(actor, 'is_authenticated', False):
        return None
    actor_pk = getattr(actor, 'pk', None)
    if type(actor_pk) is not int or actor_pk < 1:
        return None
    client = None
    for _ in range(2):
        user = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
        if user is None or not fresh_role(user, 'admin'):
            return None
        client = Client.objects.filter(
            pk=identity.pk, pk__in=explicitly_granted_client_ids(user), active=True
        ).first()
        if client is None:
            return None
    return client


def read_client(actor, *, environment, client_pk):
    """Read basic client state with native admin role and an explicit grant.

    The caller must establish a trusted authenticated principal and applicable
    request scope. A client record does not authorize any related object.
    """
    identity = ClientSourceIdentity(environment=environment, pk=client_pk)
    client = _authorized_client(actor, identity)
    if client is None:
        return None
    return ClientSourceRecord(
        identity=identity, name=client.name, code=client.code, active=client.active
    )


def read_client_details(actor, *, environment, client_pk):
    """Read direct client values, not settings, counts or related authority.

    The caller must establish a trusted principal and applicable request scope.
    Source timestamps are not a complete change feed or revision.
    """
    identity = ClientSourceIdentity(environment=environment, pk=client_pk)
    client = _authorized_client(actor, identity)
    if client is None:
        return None
    return ClientDetailsSourceRecord(
        identity=identity,
        name=client.name,
        code=client.code,
        active=client.active,
        created_at=client.created_at,
        updated_at=client.updated_at,
    )


def read_client_revision(actor, *, environment, client_pk):
    """Read an authorized native counter without a generation/completeness claim."""
    identity = ClientSourceIdentity(environment=environment, pk=client_pk)
    client = _authorized_client(actor, identity)
    if client is None:
        return None
    return ClientStructureSourceRecord(
        identity=identity, location_revision=client.location_revision
    )


def _authorized_location(actor, identity):
    """Load a twice-checked location for a trusted integration reader."""
    from django.contrib.auth import get_user_model

    from rest_framework.exceptions import PermissionDenied

    from ai.core.integrations.retrieval_authority import fresh_role
    from assets.integration_authority import explicitly_granted_client_ids
    from assets.locations import locations_for

    if actor is None or not getattr(actor, 'is_authenticated', False):
        return None
    actor_pk = getattr(actor, 'pk', None)
    if type(actor_pk) is not int or actor_pk < 1:
        return None

    location = None
    for _ in range(2):
        user = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
        if user is None or not fresh_role(user, 'work_order'):
            return None
        try:
            location = (
                locations_for(user)
                .filter(
                    pk=identity.pk, client_id__in=explicitly_granted_client_ids(user)
                )
                .first()
            )
        except PermissionDenied:
            return None
        if location is None:
            return None

    return location


def read_location(actor, *, environment, location_pk):
    """Read minimal current location state, separately from any machine access.

    This does not authorize ancestors, children, client fields or counts.
    """
    identity = LocationSourceIdentity(environment=environment, pk=location_pk)
    location = _authorized_location(actor, identity)
    if location is None:
        return None
    return LocationSourceRecord(
        identity=identity, name=location.name, archived=location.archived
    )


def read_location_details(actor, *, environment, location_pk):
    """Read direct properties, never hierarchy or counts.

    The caller must establish the trusted principal and any request/token scope.
    """
    identity = LocationSourceIdentity(environment=environment, pk=location_pk)
    location = _authorized_location(actor, identity)
    if location is None:
        return None
    return LocationDetailsSourceRecord(
        identity=identity,
        name=location.name,
        code=location.code,
        kind=location.kind,
        timezone=location.timezone,
        archived=location.archived,
        version=location.version,
    )


def read_location_properties(actor, *, environment, location_pk):
    """Read direct native properties without releasing relationship fields.

    The caller must establish the trusted principal and any request/token scope.
    Native timestamps and version are not a complete change feed.
    """
    identity = LocationSourceIdentity(environment=environment, pk=location_pk)
    location = _authorized_location(actor, identity)
    if location is None:
        return None
    return LocationPropertiesSourceRecord(
        identity=identity,
        name=location.name,
        code=location.code,
        kind=location.kind,
        description=location.description,
        timezone=location.timezone,
        archived=location.archived,
        version=location.version,
        created_at=location.created_at,
        updated_at=location.updated_at,
    )


def _authorized_machine_source(actor, identity):
    """Resolve a current machine under native scope and explicit client grant."""
    from django.contrib.auth import get_user_model

    from tasks.scope import scoped_machines

    from ai.core.integrations.retrieval_authority import fresh_role
    from assets.integration_authority import explicitly_granted_client_ids

    if actor is None or not getattr(actor, 'is_authenticated', False):
        return None
    actor_pk = getattr(actor, 'pk', None)
    if type(actor_pk) is not int or actor_pk < 1:
        return None
    user = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
    if user is None or not fresh_role(user, 'work_order'):
        return None
    machine = (
        scoped_machines(user)
        .filter(pk=identity.pk, client_id__in=explicitly_granted_client_ids(user))
        .first()
    )
    if machine is None:
        return None
    # Re-resolve user, role and machine scope after source work. The second
    # lookup also avoids exposing a stale row after a client-scope transfer.
    user = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
    if user is None or not fresh_role(user, 'work_order'):
        return None
    machine = (
        scoped_machines(user)
        .filter(pk=identity.pk, client_id__in=explicitly_granted_client_ids(user))
        .first()
    )
    if machine is None:
        return None
    return machine


def read_machine(actor, *, environment, machine_pk):
    """Return a minimal scoped machine record, or None if unavailable.

    None intentionally does not distinguish a missing machine from an
    inaccessible machine. This does not authorize associated records, fields,
    attachments, telemetry, actions, or any downstream consumer.
    """
    identity = MachineSourceIdentity(environment=environment, pk=machine_pk)
    machine = _authorized_machine_source(actor, identity)
    if machine is None:
        return None
    return MachineSourceRecord(
        identity=identity, name=machine.name, active=machine.active
    )


def read_machine_details(actor, *, environment, machine_pk):
    """Read selected native direct fields, never related identities or counts.

    The caller must bind a trusted principal and enforce request/token scope.
    Native timestamps are not a complete change feed.
    """
    identity = MachineSourceIdentity(environment=environment, pk=machine_pk)
    machine = _authorized_machine_source(actor, identity)
    if machine is None:
        return None
    return MachineDetailsSourceRecord(
        identity=identity,
        name=machine.name,
        description=machine.description,
        active=machine.active,
        asset_type=machine.asset_type,
        location=machine.location,
        manufacturer=machine.manufacturer,
        model=machine.model,
        serial=machine.serial,
        created_at=machine.created_at,
        updated_at=machine.updated_at,
    )


def read_machine_registration(actor, *, environment, machine_pk):
    """Read native registry coordinates without releasing opaque source context.

    The caller binds a trusted current principal and applicable request scope.
    Native UUIDs and source keys do not prove migration stability or provenance.
    """
    identity = MachineSourceIdentity(environment=environment, pk=machine_pk)
    machine = _authorized_machine_source(actor, identity)
    if machine is None:
        return None
    return MachineRegistrationSourceRecord(
        identity=identity,
        uuid=machine.uuid,
        source_namespace=machine.source_namespace,
        source_entity_uuid=machine.source_entity_uuid,
        source_key=machine.source_key,
    )


def read_machine_barcode(actor, *, environment, machine_pk):
    """Read authorized native barcode hash/links, withholding raw routing bytes."""
    identity = MachineSourceIdentity(environment=environment, pk=machine_pk)
    machine = _authorized_machine_source(actor, identity)
    if machine is None:
        return None
    return MachineBarcodeSourceRecord(
        identity=identity,
        barcode_hash=machine.barcode_hash,
        api_url=f'{machine.get_api_url()}{machine.pk}/',
        web_url=machine.get_absolute_url(),
    )


def read_machine_profile(actor, *, environment, machine_pk):
    """Read schema-valid direct profile JSON, or mark an invalid value withheld.

    The caller must bind a trusted principal and enforce request/token scope.
    Native profile text is untrusted and never authorizes referenced objects.
    """
    from copy import deepcopy

    from django.core.exceptions import ValidationError

    from assets.machine_profile import validate_machine_profile

    identity = MachineSourceIdentity(environment=environment, pk=machine_pk)
    machine = _authorized_machine_source(actor, identity)
    if machine is None:
        return None
    profile = deepcopy(machine.profile)
    try:
        validate_machine_profile(profile)
    except (ValidationError, TypeError, ValueError):
        status = 'invalid_withheld'
    else:
        status = 'valid'
    # The profile can change and the grant can be revoked during validation.
    # Sequential checks narrow that window but are not an atomic snapshot.
    current = _authorized_machine_source(actor, identity)
    if current is None or current.profile != profile:
        return None
    return MachineProfileSourceRecord(
        identity=identity, status=status, profile=profile if status == 'valid' else None
    )


def read_maintenance_record(actor, *, environment, record_pk):
    """Return minimal scoped maintenance history, or None if unavailable.

    Records are currently withheld until historical client attribution can
    be verified independently of the machine's current client.
    A machine or maintenance record does not authorize a linked Work order,
    attachments, personnel fields or any downstream consumer.
    """
    from django.contrib.auth import get_user_model

    from tasks.scope import ScopeError, maintenance_record_scope_filter

    from ai.core.integrations.retrieval_authority import fresh_role
    from assets import integration_attribution
    from assets.integration_authority import explicitly_granted_client_ids
    from assets.models import AssetMaintenanceRecord

    identity = MaintenanceSourceIdentity(environment=environment, pk=record_pk)
    if actor is None or not getattr(actor, 'is_authenticated', False):
        return None
    actor_pk = getattr(actor, 'pk', None)
    if type(actor_pk) is not int or actor_pk < 1:
        return None

    # Re-resolve both the actor and the scoped row before projection.
    record = None
    for _ in range(2):
        user = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
        if user is None or not fresh_role(user, 'work_order'):
            return None
        try:
            scope_filter = maintenance_record_scope_filter(user)
        except ScopeError:
            return None
        visible = AssetMaintenanceRecord.objects.filter(
            scope_filter,
            pk=identity.pk,
            machine__client_id__in=explicitly_granted_client_ids(user),
        )
        record = integration_attribution.attributed_maintenance_records(
            visible,
            **(
                {'actor': user, 'environment': environment, 'source_pk': identity.pk}
                if integration_attribution.authority_enabled()
                else {}
            ),
        ).first()
        if record is None:
            return None

    assert record is not None
    return MaintenanceSourceRecord(
        identity=identity,
        machine=MachineSourceIdentity(environment=environment, pk=record.machine_id),
        date=record.date,
        summary=record.summary,
    )


def read_machine_attachment(actor, *, environment, attachment_pk):
    """Return only an authorized machine attachment identity and owner.

    Attachments are currently withheld until their historical client
    attribution can be verified independently of current machine ownership.
    Neither a returned identity nor its owner authorizes metadata fields,
    links, bytes, other associations, or downstream consumers. Only direct
    machine owners are supported; every other owner kind is withheld.
    """
    from django.contrib.auth import get_user_model

    from tasks.scope import scoped_machines

    from ai.core.integrations.retrieval_authority import fresh_role
    from assets import integration_attribution
    from assets.integration_authority import explicitly_granted_client_ids
    from assets.models import AssetMachine
    from common.models import Attachment
    from common.validators import attachment_model_types

    identity = AttachmentSourceIdentity(environment=environment, pk=attachment_pk)
    if actor is None or not getattr(actor, 'is_authenticated', False):
        return None
    actor_pk = getattr(actor, 'pk', None)
    if type(actor_pk) is not int or actor_pk < 1:
        return None

    attachment = None
    for _ in range(2):
        user = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
        if (
            user is None
            or not fresh_role(user, 'work_order')
            or not AssetMachine.check_related_permission('view', user)
        ):
            return None
        # Generic attachment owner labels do not include an app label. Refuse
        # ambiguous or missing registrations instead of guessing their owner.
        matching_types = [
            model
            for model in attachment_model_types()
            if model.__name__.lower() == 'assetmachine'
        ]
        if matching_types != [AssetMachine]:
            return None
        machines = scoped_machines(user).filter(
            client_id__in=explicitly_granted_client_ids(user)
        )
        visible = Attachment.objects.filter(
            pk=identity.pk, model_type='assetmachine', model_id__in=machines
        )
        attachment = (
            integration_attribution
            .attributed_machine_attachments(
                visible,
                **(
                    {
                        'actor': user,
                        'environment': environment,
                        'source_pk': identity.pk,
                    }
                    if integration_attribution.authority_enabled()
                    else {}
                ),
            )
            .values('model_id')
            .first()
        )
        if attachment is None:
            return None

    assert attachment is not None
    return MachineAttachmentSourceRecord(
        identity=identity,
        machine=MachineSourceIdentity(
            environment=environment, pk=attachment['model_id']
        ),
    )


def read_machine_attachment_content(actor, *, environment, attachment_pk, max_bytes):
    """Return captured native bytes after the final current-owner check.

    The private synchronous assembly retains entry/pre-storage/return checks.
    Caller-visible DTOs cannot replace bytes or bind another identity. Every
    later use needs current authority; this is not a streaming or storage lease.
    """
    import hashlib

    from assets.integration_content_read import (
        _original_assembly,
        _OriginalWithheldError,
        _projection_matches,
    )

    record_type = MachineAttachmentContentRecord
    try:
        with _original_assembly(
            actor,
            environment=environment,
            attachment_pk=attachment_pk,
            max_bytes=max_bytes,
            owner_reader=read_machine_attachment,
            model_type='assetmachine',
            owner_field='machine',
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
                    machine=assembly.owner,
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
                owner_field='machine',
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
            owner_field='machine',
            content=content,
        ):
            raise AttachmentContentUnavailableError('Stored content is unavailable')
        return projected
    except _OriginalWithheldError:
        return None
