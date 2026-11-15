"""Owned native transfer and independent served placement across full ASGI restart."""

import json
from dataclasses import asdict


def install_placement_fixture(runtime, machines, state):
    """Restore configuration only; never rebootstrap or acknowledge on restart."""
    from assets.integration_authority import integration_actor_from_request
    from assets.integration_contracts import (
        ClientSourceIdentity,
        LocationSourceIdentity,
        MachineSourceIdentity,
    )
    from assets.integration_s5_ack import PlacementProofError, acknowledge_latest
    from assets.integration_s5_contracts import PlacementExpectation, ProjectionBinding
    from assets.integration_s5_pending import PlacementReadAdapter
    from assets.locations import save_location
    from assets.models import AssetMachine, ClientScopeGrant
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    from django.db import connection
    from integration_state.capture import capture_is_installed, install_capture
    from integration_state.models import ProjectionEnrollment, SourceNamespace
    from integration_state.services import bootstrap_projection, prepare_projection
    from integration_state.tests.test_placement_http_process import (
        RetainedServingProcess,
    )
    from tasks.scope import scoped_machines
    from users.models import RuleSet

    holder = {"serving": None}

    def authorized(request, binding, placement):
        actor = integration_actor_from_request(request, required_role="work_order")
        return (
            actor is not None
            and ClientScopeGrant.objects.filter(
                user=actor, client_id=binding.client.pk, client__active=True
            ).exists()
            and scoped_machines(actor)
            .filter(pk=placement.machine.pk, client_id=binding.client.pk)
            .exists()
        )

    def configure():
        config = state["placement"]
        enrollment = ProjectionEnrollment.objects.get(pk=config["enrollment_pk"], active=True)
        directory = runtime / "placement-serving"
        directory.mkdir(exist_ok=True)
        holder["serving"] = RetainedServingProcess(directory)
        settings.AIMMS_PLACEMENT_STATE_ADAPTER = lambda request: PlacementReadAdapter(
            actor_pk=request.user.pk,
            namespace_id=enrollment.namespace_id,
            projection=enrollment.projection,
            authorize_metadata=authorized,
            serving=holder["serving"],
        )

    if state.get("placement"):
        if not capture_is_installed(connection):
            raise RuntimeError("Placement capture did not survive native process restart")
        configure()

    def prepare():
        if state.get("placement"):
            raise ValueError("Fixture placement is already configured")
        install_capture(connection)
        if not capture_is_installed(connection):
            raise RuntimeError("Fixture capture is unverified")
        machine = AssetMachine.objects.get(pk=machines[0])
        namespace = SourceNamespace.objects.get(environment="synthetic-browser", active=True)
        requester = get_user_model().objects.get(username="native-browser-requester")
        group = Group.objects.create(name="Synthetic native placement writers")
        RuleSet.objects.update_or_create(
            group=group,
            name="work_order",
            defaults={"can_view": True, "can_change": True, "can_add": True},
        )
        requester.groups.add(group)
        requester = get_user_model().objects.get(pk=requester.pk)
        destination = save_location(
            requester,
            {
                "client": machine.client_id,
                "name": "Synthetic restart destination",
                "code": "synthetic-restart",
                "timezone": "UTC",
                "reason": "Synthetic process restart fixture",
            },
        )
        enrollment = prepare_projection(namespace.pk, "synthetic-native-restart", machine.client_id)
        bootstrap_projection(enrollment.pk)
        state["placement"] = {
            "enrollment_pk": enrollment.pk,
            "destination_pk": destination.pk,
            "machine_pk": machine.pk,
        }
        configure()
        return state["placement"]

    def stage(row, generation):
        config = state["placement"]
        if row["pk"] != config["machine_pk"]:
            raise ValueError("Only the earlier native fixture HTTP row may be staged")
        enrollment = ProjectionEnrollment.objects.select_related("namespace").get(
            pk=config["enrollment_pk"], active=True
        )
        namespace = enrollment.namespace
        value = {
            "binding": asdict(
                ProjectionBinding(
                    environment=namespace.environment,
                    namespace_id=namespace.pk,
                    namespace_epoch=namespace.epoch,
                    enrollment_id=enrollment.pk,
                    configuration_epoch=enrollment.epoch,
                    projection=enrollment.projection,
                    client=ClientSourceIdentity(namespace.environment, row["client"]),
                )
            ),
            "placement": asdict(
                PlacementExpectation(
                    machine=MachineSourceIdentity(namespace.environment, row["pk"]),
                    client=ClientSourceIdentity(namespace.environment, row["client"]),
                    location=None
                    if row["physical_location"] is None
                    else LocationSourceIdentity(
                        namespace.environment, row["physical_location"]["pk"]
                    ),
                    native_version=int(row["placement_sync"]["native_version"]),
                    fence=int(row["placement_sync"]["source_fence"]),
                    namespace_epoch=namespace.epoch,
                )
            ),
            "generation": generation,
        }
        holder["serving"].stage([json.loads(json.dumps(value, default=str))])
        return {"staged": True}

    def ack():
        config = state["placement"]
        try:
            accepted = acknowledge_latest(
                config["enrollment_pk"], config["machine_pk"], adapter=holder["serving"]
            )
        except PlacementProofError as exc:
            return {
                "acknowledged": False,
                "proof_status": "rejected",
                "error_type": type(exc).__name__,
            }
        return {
            "acknowledged": accepted,
            "proof_status": "accepted" if accepted else "not_current",
        }

    def close():
        if holder["serving"] is not None:
            holder["serving"].close()
            holder["serving"] = None

    return {"prepare": prepare, "stage": stage, "ack": ack, "close": close}
