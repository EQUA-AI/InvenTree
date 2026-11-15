"""Server-only placement proof; no HTTP mark-current or operational writes.

The adapter is a trusted, server-selected dependency, not request JSON. This
module installs no adapter or external client. Serving IO is outside native
source locks; only exact still-current source/configuration is accepted.
"""

from contextlib import contextmanager
from dataclasses import replace
from uuid import UUID

from django.db import DatabaseError, connection, transaction

from assets.integration_contracts import ClientSourceIdentity
from assets.integration_s5_contracts import (
    PlacementServingObservation,
    ProjectionBinding,
)
from assets.models import Client
from integration_state.models import (
    PlacementAcknowledgement,
    ProjectionEnrollment,
    SourceNamespace,
)
from integration_state.services import (
    PlacementStateError,
    PlacementStoreUnavailableError,
    _current_enrollment,
    _positive_id,
    _require_capture,
    _require_default_routing,
    current_expectation,
)


class PlacementProofError(PlacementStateError):
    """A claimed observation does not qualify the latest served placement."""


class PlacementProofUnavailableError(PlacementProofError):
    """The trusted proof dependency is unavailable, not successful or current."""


@contextmanager
def _metadata_transaction():
    """Retain atomic rollback and distinguish database failure from bad proof."""
    try:
        with transaction.atomic():
            yield
    except DatabaseError as exc:
        raise PlacementStoreUnavailableError(
            'Placement state store is unavailable'
        ) from exc


def _binding_snapshot(binding):
    """Detach all nested coordinates before a trusted callback receives a copy."""
    if type(binding) is not ProjectionBinding:
        raise PlacementProofError('An exact typed projection binding is required')
    binding.__post_init__()
    return replace(
        binding,
        client=replace(binding.client),
        namespace_epoch=UUID(int=binding.namespace_epoch.int),
        configuration_epoch=UUID(int=binding.configuration_epoch.int),
    )


def _observation_snapshot(observation):
    """Validate frozen-but-forgeable carriers and retain private detached meaning."""
    if type(observation) is not PlacementServingObservation:
        raise PlacementProofError('A typed serving observation is required')
    observation.__post_init__()
    placement = observation.placement
    return PlacementServingObservation(
        binding=_binding_snapshot(observation.binding),
        placement=replace(
            placement,
            machine=replace(placement.machine),
            client=replace(placement.client),
            location=None
            if placement.location is None
            else replace(placement.location),
            namespace_epoch=UUID(int=placement.namespace_epoch.int),
        ),
        generation=observation.generation,
    )


def _locked_context(enrollment_id, machine_id):
    """Follow source/state then namespace/configuration ownership ordering."""
    _require_capture()
    observed = ProjectionEnrollment.objects.filter(pk=enrollment_id).first()
    if observed is None:
        raise PlacementProofError('Projection configuration is unavailable')
    expected = current_expectation(observed.namespace_id, machine_id)
    namespace = SourceNamespace.objects.select_for_update(no_key=True).get(
        pk=observed.namespace_id
    )
    enrollment = ProjectionEnrollment.objects.select_for_update().get(pk=enrollment_id)
    _current_enrollment(enrollment, namespace)
    if (
        expected is None
        or not enrollment.active
        or not enrollment.bootstrapped
        or enrollment.bootstrap_status != 'ready'
        or enrollment.client_id != expected.client.pk
        or not Client.objects.filter(pk=enrollment.client_id, active=True).exists()
    ):
        raise PlacementProofError('Projection placement is unqualified')
    binding = ProjectionBinding(
        environment=namespace.environment,
        namespace_id=namespace.pk,
        namespace_epoch=namespace.epoch,
        enrollment_id=enrollment.pk,
        configuration_epoch=enrollment.epoch,
        projection=enrollment.projection,
        client=ClientSourceIdentity(namespace.environment, enrollment.client_id),
    )
    return binding, expected, enrollment


def acknowledge_latest(enrollment_id, machine_id, *, adapter):
    """Observe independently, then compare-and-set without an operational write."""
    _positive_id(enrollment_id)
    _positive_id(machine_id)
    _require_default_routing()
    if connection.in_atomic_block or not connection.get_autocommit():
        raise PlacementProofError(
            'Serving observation requires an outer commit boundary'
        )
    with _metadata_transaction():
        binding, expected, enrollment = _locked_context(enrollment_id, machine_id)
        enrollment.observation_revision += 1
        revision = enrollment.observation_revision
        enrollment.generation_known = False
        enrollment.save(update_fields=['observation_revision', 'generation_known'])
    try:
        observation = adapter.observe_placement(
            _binding_snapshot(binding), replace(expected.machine)
        )
        snapshot = _observation_snapshot(observation)
        if snapshot.binding != binding or snapshot.placement != expected:
            raise PlacementProofError(
                'Served placement does not match the latest source'
            )
        verification = _observation_snapshot(snapshot)
        if adapter.verify_observation(verification) is not True:
            raise PlacementProofError('Serving observation could not be verified')
        if (
            _observation_snapshot(verification) != snapshot
            or _observation_snapshot(observation) != snapshot
        ):
            raise PlacementProofError('Serving observation changed during verification')
    except PlacementStateError:
        raise
    except ValueError as exc:
        raise PlacementProofError('Malformed serving observation') from exc
    except (TimeoutError, ConnectionError, OSError) as exc:
        raise PlacementProofUnavailableError('Serving proof is unavailable') from exc
    with _metadata_transaction():
        current_binding, current, enrollment = _locked_context(
            enrollment_id, machine_id
        )
        if (
            current_binding != binding
            or current != expected
            or enrollment.observation_revision != revision
        ):
            raise PlacementProofError(
                'Source or serving observation is no longer current'
            )
        PlacementAcknowledgement.objects.update_or_create(
            enrollment=enrollment,
            source_id=machine_id,
            defaults={
                'namespace_epoch': expected.namespace_epoch,
                'source_revision': expected.fence,
                'client_id': expected.client.pk,
                'location_id': None
                if expected.location is None
                else expected.location.pk,
                'native_version': expected.native_version,
                'serving_generation': snapshot.generation,
            },
        )
        enrollment.served_generation = snapshot.generation
        enrollment.generation_known = True
        enrollment.save(update_fields=['served_generation', 'generation_known'])
    return True
