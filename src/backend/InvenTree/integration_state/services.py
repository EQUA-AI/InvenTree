"""Internal placement-state ownership; no actor or outbound transport is inferred."""

from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.db.models import BigIntegerField, Exists, F, OuterRef, Q, Value
from django.db.models.functions import Coalesce

from assets.integration_contracts import (
    ClientSourceIdentity,
    LocationSourceIdentity,
    MachineSourceIdentity,
)
from assets.integration_s5_contracts import PlacementExpectation
from assets.location_models import AssetLocation
from assets.models import AssetMachine, Client
from integration_state.capture import capture_is_installed
from integration_state.models import (
    ProjectionEnrollment,
    SourceChange,
    SourceNamespace,
    SourceState,
)


class PlacementStateError(ValueError):
    """Source placement cannot be qualified; absence is never successful freshness."""


class PlacementStoreUnavailableError(PlacementStateError):
    """A metadata dependency failed; this is not a missing or empty population."""


def _literal(value, maximum):
    """Keep server-selected source/configuration labels literal and typed."""
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise PlacementStateError('A bounded literal source label is required')


def _require_default_routing():
    """Refuse unqualified routing before an ORM manager can select a database."""
    if settings.DATABASE_ROUTERS or connection.alias != 'default':
        raise PlacementStateError('Placement capture routing is unqualified')


def _require_capture():
    """Admit only the verified default database, never unknown routers or aliases."""
    _require_default_routing()
    try:
        ready = capture_is_installed(connection)
    except DatabaseError as exc:
        raise PlacementStoreUnavailableError(
            'Placement capture store is unavailable'
        ) from exc
    except ValueError as exc:
        raise PlacementStateError('Placement capture is unqualified') from exc
    if ready is not True:
        raise PlacementStateError('Placement capture is unqualified')


def _positive_id(value):
    """Reject coercion before identifiers reach Django's permissive field lookup."""
    if type(value) is not int or value < 1:
        raise PlacementStateError('An exact positive source identifier is required')


@transaction.atomic
def current_expectation(namespace_id, machine_id):
    """Read the persisted fence under source-before-state locking, never enroll."""
    _positive_id(namespace_id)
    _positive_id(machine_id)
    _require_default_routing()
    namespace = SourceNamespace.objects.filter(pk=namespace_id).first()
    if namespace is None:
        raise PlacementStateError('Source placement namespace is unavailable')
    if not namespace.active:
        return None
    _require_capture()
    machine = AssetMachine.objects.select_for_update().filter(pk=machine_id).first()
    if machine is None:
        raise PlacementStateError('Source placement is unavailable')
    state = (
        SourceState.objects
        .select_for_update()
        .filter(
            namespace=namespace,
            epoch=namespace.epoch,
            entity_kind='machine',
            aspect='placement',
            source_id=machine_id,
        )
        .first()
    )
    if (
        state is None
        or state.deleted
        or machine.client_id is None
        or (state.client_id, state.location_id, state.native_version)
        != (machine.client_id, machine.physical_location_id, machine.placement_version)
        or (
            machine.physical_location_id is not None
            and not AssetLocation.objects.filter(
                pk=machine.physical_location_id, client_id=machine.client_id
            ).exists()
        )
    ):
        raise PlacementStateError('Source placement cannot be qualified')
    return PlacementExpectation(
        machine=MachineSourceIdentity(namespace.environment, state.source_id),
        client=ClientSourceIdentity(namespace.environment, state.client_id),
        location=None
        if state.location_id is None
        else LocationSourceIdentity(namespace.environment, state.location_id),
        native_version=state.native_version,
        fence=state.revision,
        namespace_epoch=namespace.epoch,
    )


@transaction.atomic
def activate_namespace(environment):
    """Start source-owned capture before any population can become enrolled."""
    _literal(environment, 128)
    _require_capture()
    namespace, _ = SourceNamespace.objects.get_or_create(environment=environment)
    namespace = SourceNamespace.objects.select_for_update(no_key=True).get(
        pk=namespace.pk
    )
    if SourceNamespace.objects.filter(active=True).exclude(pk=namespace.pk).exists():
        raise PlacementStateError('Another source environment owns active capture')
    if not namespace.active:
        namespace.epoch = uuid4()
        namespace.active = True
        namespace.save(update_fields=['epoch', 'active'])
    return namespace


@transaction.atomic
def prepare_projection(namespace_id, projection, client_id):
    """Prepare an inactive server-owned configuration, never an actor enrollment."""
    _positive_id(namespace_id)
    _positive_id(client_id)
    _literal(projection, 128)
    _require_capture()
    namespace = SourceNamespace.objects.select_for_update(no_key=True).get(
        pk=namespace_id
    )
    if (
        not namespace.active
        or not Client.objects.filter(pk=client_id, active=True).exists()
    ):
        raise PlacementStateError('Projection source population is unqualified')
    ProjectionEnrollment.objects.filter(
        namespace=namespace, projection=projection, client_id=client_id
    ).update(active=False, bootstrap_status='superseded')
    return ProjectionEnrollment.objects.create(
        namespace=namespace,
        namespace_epoch=namespace.epoch,
        projection=projection,
        client_id=client_id,
    )


@transaction.atomic
def disable_namespace(namespace_id):
    """Withdraw capture admission without removing source or retained evidence."""
    _positive_id(namespace_id)
    _require_default_routing()
    namespace = (
        SourceNamespace.objects
        .select_for_update(no_key=True)
        .filter(pk=namespace_id)
        .first()
    )
    if namespace is None:
        raise PlacementStateError('Source namespace is unavailable')
    namespace.active = False
    namespace.save(update_fields=['active'])
    ProjectionEnrollment.objects.filter(namespace=namespace).update(
        active=False,
        bootstrap_status='disabled',
        generation_known=False,
        observation_revision=F('observation_revision') + 1,
    )
    return namespace


@transaction.atomic
def disable_projection(enrollment_id):
    """Withdraw this configuration; re-enable requires a fresh preparation epoch."""
    _positive_id(enrollment_id)
    _require_default_routing()
    enrollment = (
        ProjectionEnrollment.objects
        .select_for_update()
        .filter(pk=enrollment_id)
        .first()
    )
    if enrollment is None:
        raise PlacementStateError('Projection configuration is unavailable')
    enrollment.active = False
    enrollment.bootstrap_status = 'disabled'
    enrollment.generation_known = False
    enrollment.observation_revision = F('observation_revision') + 1
    enrollment.save(
        update_fields=[
            'active',
            'bootstrap_status',
            'generation_known',
            'observation_revision',
        ]
    )
    enrollment.refresh_from_db(fields=['observation_revision'])
    return enrollment


def _current_enrollment(enrollment, namespace):
    """Old epochs and superseded preparations cannot reactivate a population."""
    if (
        not namespace.active
        or enrollment.namespace_epoch != namespace.epoch
        or enrollment.bootstrap_status not in ('preparing', 'failed', 'ready')
        or ProjectionEnrollment.objects.filter(
            namespace=namespace,
            projection=enrollment.projection,
            client_id=enrollment.client_id,
            pk__gt=enrollment.pk,
        ).exists()
    ):
        raise PlacementStateError('Projection configuration is no longer current')


def bootstrap_projection(enrollment_id, *, batch_size=100):
    """Retain a failed/incomplete outcome outside the rolled-back batch itself."""
    _positive_id(enrollment_id)
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise PlacementStateError('An exact bounded bootstrap batch is required')
    _require_default_routing()
    observed = ProjectionEnrollment.objects.filter(pk=enrollment_id).first()
    if observed is None:
        raise PlacementStateError('Projection configuration is unavailable')
    try:
        return _bootstrap_projection(enrollment_id, observed.epoch, batch_size)
    except PlacementStateError:
        ProjectionEnrollment.objects.filter(
            pk=enrollment_id,
            epoch=observed.epoch,
            active=False,
            bootstrap_status__in=('preparing', 'failed'),
        ).update(bootstrap_status='failed')
        raise


def _population_complete(enrollment):
    """Use a DB-side anti-join, not a materialized enterprise list or row count."""
    if not Client.objects.filter(pk=enrollment.client_id, active=True).exists():
        return False
    marker = (
        SourceState.objects
        .filter(
            namespace_id=enrollment.namespace_id,
            epoch=enrollment.namespace_epoch,
            entity_kind='machine',
            aspect='placement',
            source_id=OuterRef('pk'),
            client_id=OuterRef('client_id'),
            native_version=OuterRef('placement_version'),
            revision__gt=0,
            deleted=False,
        )
        .annotate(
            _location_key=Coalesce(
                'location_id', Value(-1), output_field=BigIntegerField()
            )
        )
        .filter(
            _location_key=Coalesce(
                OuterRef('physical_location_id'),
                Value(-1),
                output_field=BigIntegerField(),
            )
        )
    )
    location = AssetLocation.objects.filter(
        pk=OuterRef('physical_location_id'), client_id=enrollment.client_id
    )
    return (
        not AssetMachine.objects
        .filter(client_id=enrollment.client_id)
        .annotate(_captured=Exists(marker), _location_valid=Exists(location))
        .filter(
            Q(_captured=False)
            | (Q(physical_location_id__isnull=False) & Q(_location_valid=False))
            | Q(pk__lte=0)
            | Q(placement_version__lt=0)
        )
        .exists()
    )


@transaction.atomic
def _bootstrap_projection(enrollment_id, configuration_epoch, batch_size):
    """Commit one bounded keyset batch; no native field or lock counter is edited."""
    _require_capture()
    observed = ProjectionEnrollment.objects.select_related('namespace').get(
        pk=enrollment_id
    )
    if observed.epoch != configuration_epoch:
        raise PlacementStateError('Projection configuration changed during baseline')
    _current_enrollment(observed, observed.namespace)
    if observed.active and observed.bootstrapped:
        return {'processed': 0, 'complete': True}
    source_ids = list(
        AssetMachine.objects
        .filter(client_id=observed.client_id, pk__gt=observed.bootstrap_cursor)
        .order_by('pk')
        .values_list('pk', flat=True)[: batch_size + 1]
    )
    processed = 0
    for source_id in source_ids[:batch_size]:
        machine = AssetMachine.objects.select_for_update().filter(pk=source_id).first()
        if machine is None or machine.client_id != observed.client_id:
            continue
        state, created = SourceState.objects.get_or_create(
            namespace_id=observed.namespace_id,
            epoch=observed.namespace_epoch,
            entity_kind='machine',
            aspect='placement',
            source_id=machine.pk,
            defaults={
                'client_id': machine.client_id,
                'location_id': machine.physical_location_id,
                'native_version': machine.placement_version,
            },
        )
        expected = current_expectation(observed.namespace_id, machine.pk)
        if expected is None:
            raise PlacementStateError(
                'Source placement became unqualified during baseline'
            )
        if created:
            SourceChange.objects.create(
                namespace_id=observed.namespace_id,
                epoch=observed.namespace_epoch,
                entity_kind='machine',
                aspect='placement',
                source_id=machine.pk,
                revision=state.revision,
                operation='baseline',
                previous=None,
                current={
                    'client_id': machine.client_id,
                    'location_id': machine.physical_location_id,
                    'native_version': machine.placement_version,
                },
            )
        processed += 1
    namespace = SourceNamespace.objects.select_for_update(no_key=True).get(
        pk=observed.namespace_id
    )
    current = ProjectionEnrollment.objects.select_for_update().get(pk=enrollment_id)
    _current_enrollment(current, namespace)
    if current.active and current.bootstrapped:
        return {'processed': 0, 'complete': True}
    if source_ids[:batch_size]:
        current.bootstrap_cursor = max(
            current.bootstrap_cursor, source_ids[:batch_size][-1]
        )
    current.bootstrapped = len(source_ids) <= batch_size
    if current.bootstrapped and not _population_complete(current):
        raise PlacementStateError('Source placement baseline is incomplete')
    _require_capture()
    current.active = current.bootstrapped
    current.bootstrap_status = 'ready' if current.bootstrapped else 'preparing'
    current.save(
        update_fields=['bootstrap_cursor', 'bootstrapped', 'active', 'bootstrap_status']
    )
    return {'processed': processed, 'complete': current.bootstrapped}
