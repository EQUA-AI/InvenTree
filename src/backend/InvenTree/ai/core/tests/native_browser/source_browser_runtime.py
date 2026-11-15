"""Owned model-free fixture enrollment; native auth/source/turn/storage stay real."""

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from dataclasses import replace


def install_fixture(runtime, users, machines, *, restoring=False):
    """Enroll one bounded synthetic recorded source, never a request authorizer."""
    from ai.core.config import get_settings
    from ai.core.integrations.source_reads import (
        ContentReadSelection,
        HistoryReadSelection,
        SourceReadAdapter,
    )
    from ai.core.memory.conversation import ConversationManager
    from ai.core.workflows import root as workflow
    from asgiref.sync import sync_to_async
    from assets.activation import point_hash
    from assets.health_models import HealthSource, MachineSignalBinding
    from assets.models import AssetMachine, ClientScopeGrant, DictionaryPoint
    from django.conf import settings
    from machine_health.integration_contracts import TelemetrySourceIdentity
    from machine_health.services.integration_history import HistoryWindow
    from machine_health.services.integration_s6_adapter import (
        FixtureHistoryAdapter,
        FixtureMapping,
    )
    from machine_health.tests.test_integration_s6_wire import observation

    configured = get_settings()
    configured.single_site_policy_key = "synthetic-source-browser-boundary"
    configured.policy_version = "synthetic-source-browser-v1"
    configured.allowed_origins = ["http://127.0.0.1:56311"]
    configured.feature_agui_endpoint = True
    configured.feature_ai_analysis_router_shadow = False
    configured.feature_ai_analysis_router_enforce = False
    machine = AssetMachine.objects.get(pk=machines[0])
    station = machine.parent
    state_file = runtime / "fixture-source-state.json"
    if restoring:
        state = json.loads(state_file.read_text())
        if state["schema"] != "aimms.synthetic-source-browser-state.v1":
            raise ValueError("Unknown owned fixture source state")
        source = HealthSource.objects.get(pk=state["source_pk"])
        point = DictionaryPoint.objects.get(pk=state["point_pk"])
        binding = MachineSignalBinding.objects.get(pk=state["binding_pk"])
    else:
        source = HealthSource.objects.create(
            name="Synthetic browser recorded source",
            client=machine.client,
            source_type="scada",
            connector_type="cosmos_pumphouse",
            config={"stations": [str(station.source_entity_uuid)]},
        )
        point = DictionaryPoint.objects.create(
            station=station,
            machine=machine,
            path="/browser/recorded",
            raw_tag="BROWSER_SYNTHETIC",
            display_name="Synthetic browser reading",
            data_type="number",
            unit="degC",
            unit_status="verified",
            status="approved",
        )
        binding = MachineSignalBinding.objects.create(
            machine=machine,
            source=source,
            dictionary_point=point,
            dictionary_hash=point_hash(point),
            external_key=point.path,
            display_name=point.display_name,
            unit="degC",
            active=False,
        )
        state = {
            "schema": "aimms.synthetic-source-browser-state.v1",
            "source_pk": source.pk,
            "point_pk": point.pk,
            "binding_pk": binding.pk,
            "history_value": 37.125,
            "operation": "history",
            "adapter_enabled": True,
        }
    identity = TelemetrySourceIdentity(
        environment="synthetic-browser",
        machine_pk=machine.pk,
        source_pk=source.pk,
        binding_pk=binding.pk,
    )
    window = HistoryWindow(-1, 4)
    record = observation(value=37.125, source=identity, external_key=point.path, unit="degC")
    mapping = FixtureMapping(
        client_pk=machine.client_id,
        station_pk=station.pk,
        dictionary_point_pk=point.pk,
        station_uuid=str(station.source_entity_uuid),
        external_key=point.path,
        unit=point.unit,
        dictionary_hash=binding.dictionary_hash,
        effective_window=window,
    )

    def selection(value):
        return HistoryReadSelection(
            environment=identity.environment,
            machine_pk=machine.pk,
            source_pk=source.pk,
            binding_pk=binding.pk,
            window=window,
            adapter=FixtureHistoryAdapter.from_records(
                source=identity,
                mapping=mapping,
                coverage=window,
                generation="fixture-generation",
                records=(replace(record, sample=replace(record.sample, value=value)),),
            ),
        )

    from content_browser_fixture import create_subjects

    if restoring:
        settings.AIMMS_INTEGRATION_AUTHORITY_ENABLED = True
        subjects = state["subjects"]
    else:
        subjects = create_subjects(runtime, users[0], machine)
        state["subjects"] = subjects
    history = {"selection": selection(state["history_value"])}
    current = {"selection": history["selection"]}

    def restore(coordinates):
        if coordinates.get("kind") == "history":
            return history["selection"]
        operation = coordinates.get("operation")
        expected = (
            subjects["work"]
            if operation in ("work.core", "work.context")
            else subjects["maintenance"]
            if operation == "maintenance.direct"
            else subjects["attachment"]
            if operation in ("work_document.metadata", "work_document.content")
            else None
        )
        if expected is None or coordinates.get("source_pk") != expected:
            return None
        return ContentReadSelection(
            environment=identity.environment,
            operation=operation,
            source_pk=expected,
            **({"max_bytes": 1000} if operation.endswith(".content") else {}),
        )

    if state["operation"] != "history":
        operation = state["operation"]
        current["selection"] = restore({
            "operation": operation,
            "source_pk": subjects["work"]
            if operation in ("work.core", "work.context")
            else subjects["maintenance"]
            if operation == "maintenance.direct"
            else subjects["attachment"],
        })
    adapter = SourceReadAdapter(select=lambda _: current["selection"], restore=restore)
    settings.AIMMS_AI_SOURCE_READ_ADAPTER = adapter if state["adapter_enabled"] else None

    def save_state():
        temporary = state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n")
        temporary.replace(state_file)

    save_state()
    from placement_browser_fixture import install_placement_fixture

    placement = install_placement_fixture(runtime, machines, state)

    class ForbiddenModel:
        async def route(self, **kwargs):
            raise AssertionError("Fixture forbids model/router fallback")

        def get_workflow(self, *args, **kwargs):
            raise AssertionError("Fixture forbids model/workflow fallback")

    forbidden = ForbiddenModel()
    workflow._root_workflow = workflow.RootWorkflow(forbidden, forbidden, ConversationManager())
    import ai.core.app as ai_app

    def apply(command):
        kind = command["kind"]
        if kind in ("prepare_placement_restart", "stage_placement", "ack_placement"):
            result = (
                placement["prepare"]()
                if kind == "prepare_placement_restart"
                else placement["stage"](command["row"], command["generation"])
                if kind == "stage_placement"
                else placement["ack"]()
            )
            save_state()
            return {
                "sequence": command["sequence"],
                "kind": kind,
                "applied": True,
                **result,
            }
        if kind == "revoke_a":
            ClientScopeGrant.objects.filter(user_id=users[0], client_id=machine.client_id).delete()
        elif kind == "restore_a":
            ClientScopeGrant.objects.get_or_create(user_id=users[0], client_id=machine.client_id)
        elif kind == "disable_adapter":
            settings.AIMMS_AI_SOURCE_READ_ADAPTER = None
            state["adapter_enabled"] = False
        elif kind == "enable_adapter":
            settings.AIMMS_AI_SOURCE_READ_ADAPTER = adapter
            state["adapter_enabled"] = True
        elif kind == "change_source":
            state["history_value"] = 98.625
            state["operation"] = "history"
            history["selection"] = selection(state["history_value"])
            current["selection"] = history["selection"]
        elif kind == "select_history":
            current["selection"] = history["selection"]
            state["operation"] = "history"
        elif kind in (
            "select_work",
            "select_context",
            "select_maintenance",
            "select_document",
        ):
            operation = {
                "select_work": "work.core",
                "select_context": "work.context",
                "select_maintenance": "maintenance.direct",
                "select_document": "work_document.content",
            }[kind]
            state["operation"] = operation
            current["selection"] = restore({
                "kind": "content",
                "operation": operation,
                "source_pk": subjects["work"]
                if kind in ("select_work", "select_context")
                else subjects["maintenance"]
                if kind == "select_maintenance"
                else subjects["attachment"],
            })
        elif kind == "check_unproven":
            from django.contrib.auth import get_user_model
            from tasks.integration_s3_documents import read_work_document_content
            from tasks.integration_sources import read_work_order

            actor = get_user_model().objects.get(pk=users[0])
            return {
                "sequence": command["sequence"],
                "kind": kind,
                "applied": True,
                "work_available": read_work_order(
                    actor, environment=identity.environment, work_pk=subjects["work"]
                )
                is not None,
                "original_available": read_work_document_content(
                    actor,
                    environment=identity.environment,
                    attachment_pk=subjects["attachment"],
                    max_bytes=1000,
                )
                is not None,
            }
        else:
            raise ValueError("Unknown owned fixture command")
        save_state()
        return {"sequence": command["sequence"], "kind": kind, "applied": True}

    async def commands():
        result_file = runtime / "fixture-command-result.json"
        sequence = (
            json.loads(result_file.read_text())["sequence"]
            if restoring and result_file.exists()
            else 0
        )
        while True:
            path = runtime / "fixture-command.ready.json"
            if path.exists():
                command = json.loads(path.read_text())
                if command["sequence"] > sequence:
                    try:
                        result = await sync_to_async(apply, thread_sensitive=True)(command)
                    except Exception as exc:
                        import traceback

                        (runtime / "fixture-command-error.txt").write_text(traceback.format_exc())
                        (runtime / "fixture-command-result.json").write_text(
                            json.dumps({
                                "sequence": command["sequence"],
                                "applied": False,
                                "failed": True,
                                "error_type": type(exc).__name__,
                            })
                            + "\n"
                        )
                        raise
                    sequence = command["sequence"]
                    (runtime / "fixture-command-result.json").write_text(json.dumps(result) + "\n")
            await asyncio.sleep(0.05)

    @asynccontextmanager
    async def initialize(_deadline):
        task = asyncio.create_task(commands())
        try:
            yield
        finally:
            task.cancel()
            await sync_to_async(placement["close"], thread_sensitive=True)()
            with suppress(asyncio.CancelledError):
                await task

    ai_app.initialize_runtime = initialize
    return {
        "subjects": subjects,
        "source_value": 37.125,
        "changed_value": 98.625,
        "profile": "Fixture startup replaces external model boot only; actual runtime supervisor/auth/native source/normalized turn/repository/transport; no provider readiness claim",
    }
