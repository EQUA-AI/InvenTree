"""Invocation-local S6 fixture consumer for the native evidence-analysis path.

Only trusted server code selects the source adapter/environment/coordinates.
Nothing registers a tool, enables a source, changes ordinary routing, uses a
shared cache or makes an external provider/model call. Original source bytes and
identities stay private; every supported disclosure rechecks the original real
ASGI invocation, native source authority and independently validated bytes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from ai.core.analysis.evidence import CalculationOutput, EvidenceStore, FactValue, TypedFact
from ai.core.analysis.renderer import (
    assign_ordinals,
    recorded_history_templates,
    render_answer,
)
from ai.core.analysis.schemas import (
    AnalysisClaim,
    AnalysisFacet,
    CanonicalEvidenceAnalysisResponseV2,
    IncompleteReason,
)
from ai.core.analysis.scope_context import TurnScopeContext, turn_scope_context
from ai.core.analysis.validator import CheckOutcome, validate_analysis
from ai.core.streaming import AGUIEvent, EventEmitter, EventType, RunContext
from asgiref.sync import sync_to_async
from assets.ai_read import fence
from machine_health.services.integration_history import (
    MAX_HISTORY_READS,
    MAX_HISTORY_SAMPLES_PER_READ,
    MAX_HISTORY_SAMPLES_TOTAL,
    HistoryAuthorizationError,
)
from machine_health.services.integration_s6_adapter import HistoryCapabilityError
from machine_health.services.integration_s6_reader import read_recorded_history_for_current_ai
from machine_health.services.integration_s6_wire import SourceSerializationError, bounded_json

MAX_ANALYSIS_OBSERVATIONS = 16
MAX_SOURCE_TEXT = 512
MAX_MODEL_BYTES = 64 * 1024
_TEMPLATE_KEYS = (
    "analysis.recorded_history_window",
    "analysis.recorded_history_sample",
    "analysis.recorded_history_count",
)
_STORE_TYPE, _FACT_TYPE, _VALUE_TYPE = EvidenceStore, TypedFact, FactValue
_CALCULATION_TYPE = CalculationOutput
_EVENT_TYPE = AGUIEvent
_CANONICAL_TYPE = CanonicalEvidenceAnalysisResponseV2
_CALCULATION_FIELDS = (
    "operation",
    "input_refs",
    "evidence_set_handle",
    "complete_population",
)
_FACT_FIELDS = (
    "kind",
    "source_class",
    "source_id",
    "source_revision",
    "locator",
    "retrieval_id",
    "as_of",
    "authorization_class",
    "entity_refs",
    "machine_id",
    "controlled",
)
_VIEW_FIELDS = (
    "fact_id",
    "kind",
    "source_class",
    "source_id",
    "source_revision",
    "as_of",
    "controlled",
    "entity_refs",
)


def _same(actual, expected):
    """Compare only exact builtin types, not overloaded/coercing equality."""
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return (
            all(type(key) is str for key in actual)
            and actual.keys() == expected.keys()
            and all(_same(actual[key], value) for key, value in expected.items())
        )
    if type(expected) in (tuple, list):
        return len(actual) == len(expected) and all(
            _same(a, b) for a, b in zip(actual, expected, strict=True)
        )
    if type(expected) is float:
        return actual.hex() == expected.hex()
    return actual == expected


def _encode_bound(value, *, max_bytes=MAX_MODEL_BYTES):
    """Bind returned bytes to a detached input snapshot, not the encoder's claim."""
    expected = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    payload = bounded_json(value, max_bytes=max_bytes)
    if type(payload) is not bytes or not _same(json.loads(payload), expected):
        raise SourceSerializationError("Recorded history encoded projection is unavailable")
    return payload


def _text(value):
    if value is None:
        return None
    if type(value) is not str or len(value) > MAX_SOURCE_TEXT:
        raise HistoryCapabilityError("Recorded history text projection is unsupported")
    # The native fence strips outer whitespace. Quote the original scalar
    # first so null, empty and whitespace-only metadata stay distinguishable.
    encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded) > MAX_SOURCE_TEXT:
        raise HistoryCapabilityError("Recorded history text projection is unsupported")
    return fence(encoded, limit=MAX_SOURCE_TEXT)


def _measurement(value):
    kind = type(value)
    if value is None:
        return ("text", None), "null"
    if kind is bool:
        return ("bool", value), "boolean"
    if kind is int:
        return ("int", value), "integer"
    if kind is float:
        # Native decimal rendering strips trailing zero characters even from
        # an exponent. The existing numeric quantity renderer keeps the actual
        # float spelling without coercion; its unit remains separately sourced.
        return ("unit_quantity", value), "number"
    if kind is str:
        return ("text", _text(value)), "fenced_text"
    raise HistoryCapabilityError("Recorded history value projection is unsupported")


def _definitions(body, nonce, captured_at):
    """Build independent raw specifications from private qualified source bytes."""
    records = body["observations"]
    if len(records) > MAX_ANALYSIS_OBSERVATIONS:
        raise HistoryCapabilityError("Recorded history analysis bound is unsupported")
    coverage = {
        "requested_window": body["requested_window"],
        "complete_intervals": body["complete_intervals"],
        "gaps": body["gaps"],
        "source_window_complete": body["complete"],
        "returned_count": len(records),
        "complete_population": False,
        "revision_complete": False,
        "telemetry_presentation": "recorded_history",
        "recorded_telemetry_as_live": False,
        "display_shifted": False,
        "source_evidence_kind": "synthetic_fixture",
        "consumer_captured_at": captured_at,
    }
    common = {
        "source_class": "recorded_telemetry",
        # An invocation-local observation reference, NOT provider revision proof.
        "source_revision": "observation_" + nonce,
        "retrieval_id": "ret_" + nonce,
        "as_of": captured_at,
        "authorization_class": "maintenance_authorized",
        "entity_refs": (),
        "machine_id": body["context"]["machine_pk"],
        "controlled": False,
    }
    definitions = [
        {
            **common,
            "kind": "coverage",
            "source_id": "window_" + nonce,
            "locator": {"field": "recorded_history_window"},
            "values": {
                "window_start_ms": ("int", body["requested_window"][0]),
                "window_end_ms": ("int", body["requested_window"][1]),
                "returned_count": ("int", len(records)),
                "window_complete": ("bool", body["complete"]),
                "population_complete": ("bool", False),
            },
        }
    ]
    for index, record in enumerate(records):
        sample = record["sample"]
        value, kind = _measurement(sample["value"])
        definitions.append({
            **common,
            "kind": "record_field",
            "source_id": f"sample_{nonce}_{index}",
            "locator": {"field": "recorded_observation"},
            "values": {
                "value": value,
                "value_kind": ("enum", kind),
                "observed_at": ("datetime", sample["observed_at"]),
                "ingested_at": ("datetime", record["ingested_at"]),
                "unit": ("text", _text(sample["unit"])),
                "quality": ("text", _text(sample["quality"])),
                **{
                    key: ("enum", sample[key])
                    for key in ("value_origin", "time_origin", "unit_origin", "quality_origin")
                },
                "ingest_time_origin": ("enum", record["ingest_time_origin"]),
            },
        })
    return definitions, coverage


def _selection(machine_pk):
    """Honor the native turn's narrowing selection before any source dispatch."""
    if type(machine_pk) is not int or machine_pk < 1:
        raise ValueError("Positive native source coordinates are required")
    scope = turn_scope_context.get()
    if scope is not None and (
        type(scope) is not TurnScopeContext
        or (scope.explicit and machine_pk not in scope.machine_ids)
    ):
        raise HistoryAuthorizationError("Recorded history selection is unavailable")
    if scope is not None and (
        scope.date_from is not None or scope.date_to is not None or scope.source_classes
    ):
        # Native scope's admitted source-class/date vocabulary does not yet map
        # recorded telemetry. Do not reinterpret it or read outside it; adopting
        # that shared scope/UI vocabulary is outside this exact allocation.
        raise HistoryCapabilityError("Recorded history selection is unsupported")
    return scope


def _calculation_specifications(definitions, coverage):
    """Count only a qualified complete window; a partial prefix has no total."""
    if coverage["source_window_complete"] is not True:
        return []
    return [
        {
            "operation": "count",
            "input_refs": tuple(f"fact_{index}" for index in range(2, len(definitions) + 1)),
            "evidence_set_handle": None,
            "complete_population": False,
            "values": {"count": ("int", coverage["returned_count"])},
        }
    ]


class RecordedHistoryAnalysis:
    """Private working evidence for one current authenticated invocation.

    This object is neither an HTTP/model carrier nor a reusable authorization
    grant. Native facts are checked against newly rebuilt independent source
    specifications, not an earlier successful view or two equally forged DTOs.
    """

    __slots__ = ("_captured_at", "_machine_pk", "_nonce", "_scope", "_source", "_store")

    def __init__(self, source, *, scope, machine_pk):
        self._scope, self._machine_pk = scope, machine_pk
        self._source = source
        self._nonce = uuid4().hex
        self._captured_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._store = EvidenceStore()
        if self.state != "available":
            return
        if source.reauthorize() is not True:
            raise HistoryAuthorizationError("Recorded history is unavailable")
        definitions, coverage = self._specifications()
        for definition in definitions:
            arguments = {key: definition[key] for key in _FACT_FIELDS}
            self._store.add_fact(
                **arguments,
                values={
                    key: FactValue(kind, raw) for key, (kind, raw) in definition["values"].items()
                },
            )
        self._store.set_primary_coverage(coverage)
        for definition in _calculation_specifications(definitions, coverage):
            self._store.add_calculation(
                **{key: definition[key] for key in _CALCULATION_FIELDS},
                values={
                    key: FactValue(kind, raw) for key, (kind, raw) in definition["values"].items()
                },
            )
        self._guard()

    @property
    def state(self):
        return self._source.outcome.state

    def _specifications(self):
        return _definitions(
            json.loads(self._source.outcome.payload), self._nonce, self._captured_at
        )

    def _guard(self):
        if _selection(self._machine_pk) is not self._scope:
            raise HistoryAuthorizationError("Recorded history selection is unavailable")
        if self.state == "unsupported":
            raise HistoryCapabilityError("Recorded history is unsupported")
        if self.state != "available" or self._source.reauthorize() is not True:
            raise HistoryAuthorizationError("Recorded history is unavailable")
        definitions, coverage = self._specifications()
        store = self._store
        if type(store) is not _STORE_TYPE or type(store.facts) is not dict:
            raise SourceSerializationError("Recorded history evidence is unavailable")
        if tuple(store.facts) != tuple(f"fact_{index}" for index in range(1, len(definitions) + 1)):
            raise SourceSerializationError("Recorded history evidence is unavailable")
        for index, definition in enumerate(definitions, 1):
            fact = store.facts[f"fact_{index}"]
            if type(fact) is not _FACT_TYPE or type(fact.values) is not dict:
                raise SourceSerializationError("Recorded history evidence is unavailable")
            expected = {key: definition[key] for key in _FACT_FIELDS}
            actual = {key: getattr(fact, key) for key in _FACT_FIELDS}
            if not _same(actual, expected) or not _same(fact.fact_id, f"fact_{index}"):
                raise SourceSerializationError("Recorded history evidence is unavailable")
            if fact.values.keys() != definition["values"].keys():
                raise SourceSerializationError("Recorded history evidence is unavailable")
            for key, (kind, raw) in definition["values"].items():
                value = fact.values[key]
                if type(value) is not _VALUE_TYPE or not _same(
                    (value.value_type, value.raw, value.unit), (kind, raw, None)
                ):
                    raise SourceSerializationError("Recorded history evidence is unavailable")
        calculations = _calculation_specifications(definitions, coverage)
        if type(store.calculations) is not dict or tuple(store.calculations) != tuple(
            f"calc_{index}" for index in range(1, len(calculations) + 1)
        ):
            raise SourceSerializationError("Recorded history calculation is unavailable")
        for index, definition in enumerate(calculations, 1):
            calculation = store.calculations[f"calc_{index}"]
            if (
                type(calculation) is not _CALCULATION_TYPE
                or type(calculation.values) is not dict
                or not _same(calculation.calculation_id, f"calc_{index}")
            ):
                raise SourceSerializationError("Recorded history calculation is unavailable")
            expected = {key: definition[key] for key in _CALCULATION_FIELDS}
            actual = {key: getattr(calculation, key) for key in _CALCULATION_FIELDS}
            if (
                not _same(actual, expected)
                or calculation.values.keys() != definition["values"].keys()
            ):
                raise SourceSerializationError("Recorded history calculation is unavailable")
            for key, (kind, raw) in definition["values"].items():
                value = calculation.values[key]
                if type(value) is not _VALUE_TYPE or not _same(
                    (value.value_type, value.raw, value.unit), (kind, raw, None)
                ):
                    raise SourceSerializationError("Recorded history calculation is unavailable")
        if store.evidence_sets or store.envelopes or not _same(store.coverage_meta(), coverage):
            raise SourceSerializationError("Recorded history evidence is unavailable")
        if self._source.reauthorize() is not True:
            raise HistoryAuthorizationError("Recorded history is unavailable")

    def _model_facts(self, native):
        """Allow named slots only and bind their values back to actual facts."""
        rows = native["facts"]
        if type(rows) is not list or len(rows) != len(self._store.facts):
            raise SourceSerializationError("Recorded history model projection is unavailable")
        projected = []
        for row, fact in zip(rows, self._store.facts.values(), strict=True):
            expected = {key: getattr(fact, key) for key in _VIEW_FIELDS}
            expected["entity_refs"] = list(fact.entity_refs)
            metadata = {key: row[key] for key in _VIEW_FIELDS}
            values = {key: row["values"][key] for key in fact.values}
            if not _same(metadata, expected) or not _same(values, fact.rendered_values()):
                raise SourceSerializationError("Recorded history model projection is unavailable")
            projected.append({**metadata, "values": values})
        return projected

    def _model_calculations(self, native):
        """Native growth cannot add undisclosed calculation metadata or values."""
        rows = native["calculations"]
        if type(rows) is not list or len(rows) != len(self._store.calculations):
            raise SourceSerializationError("Recorded history model calculation is unavailable")
        projected = []
        for row, calculation in zip(rows, self._store.calculations.values(), strict=True):
            expected = {
                "calculation_id": calculation.calculation_id,
                "operation": calculation.operation,
                "evidence_set": calculation.evidence_set_handle,
                "complete_population": calculation.complete_population,
                "values": calculation.rendered_values(),
            }
            selected = {key: row[key] for key in expected if key != "values"}
            selected["values"] = {key: row["values"][key] for key in calculation.values}
            if not _same(selected, expected):
                raise SourceSerializationError("Recorded history model calculation is unavailable")
            projected.append(selected)
        return projected

    def synthesis_view(self):
        """Return only the named native AI fields, freshly authorized and detached."""
        self._guard()
        try:
            native = self._store.synthesis_view(template_keys=_TEMPLATE_KEYS)
            view = {
                "facts": self._model_facts(native),
                "calculations": self._model_calculations(native),
                "evidence_sets": [],
                "coverage": self._store.coverage_meta(),
                "render_templates": list(_TEMPLATE_KEYS),
            }
            detached = json.loads(_encode_bound(view, max_bytes=MAX_MODEL_BYTES))
        except MemoryError:
            raise
        except Exception:
            self._guard()
            raise SourceSerializationError(
                "Recorded history model projection is unavailable"
            ) from None
        self._guard()
        return detached

    def render(self):
        """Use native claims, citation ordinals, all checks and canonical schema."""
        self._guard()
        try:
            coverage = self._store.coverage_meta()
            state = "complete" if coverage["source_window_complete"] else "partial"
            reasons = (
                []
                if state == "complete"
                else [{"code": "capability_boundary", "facet": "recorded_history"}]
            )
            claims = []
            for index, fact in enumerate(self._store.facts.values(), 1):
                claims.append(
                    AnalysisClaim.model_validate_json(
                        json.dumps({
                            "claim_id": f"claim_{index}",
                            "claim_role": "answer"
                            if fact.kind == "record_field" or len(self._store.facts) == 1
                            else "context",
                            "claim_type": "direct_source_fact",
                            "evidence_classification": "documented",
                            "fact_refs": [fact.fact_id],
                            "calculation_output_refs": [],
                            "evidence_refs": [],
                            "entity_refs": [],
                            "render_template": _TEMPLATE_KEYS[0 if fact.kind == "coverage" else 1],
                            "paraphrase": "",
                        })
                    )
                )
            for calculation in self._store.calculations.values():
                claims.append(
                    AnalysisClaim.model_validate_json(
                        json.dumps({
                            "claim_id": f"claim_{len(claims) + 1}",
                            "claim_role": "answer",
                            "claim_type": "calculation",
                            "evidence_classification": "calculated",
                            "fact_refs": ["fact_1", *calculation.input_refs],
                            "calculation_output_refs": [calculation.calculation_id],
                            "evidence_refs": [],
                            "entity_refs": [],
                            "render_template": _TEMPLATE_KEYS[2],
                            "paraphrase": "",
                        })
                    )
                )
            claim_ids = [claim.claim_id for claim in claims]
            facets = [
                {
                    "name": "recorded_history",
                    "status": "answered" if state == "complete" else "incomplete",
                    "claim_ids": claim_ids,
                }
            ]
            # Materialize canonical facets/reasons before the native gate.
            typed_facets = [
                AnalysisFacet.model_validate_json(json.dumps(value)) for value in facets
            ]
            typed_reasons = [
                IncompleteReason.model_validate_json(json.dumps(value)) for value in reasons
            ]
            with recorded_history_templates():
                ordinals = assign_ordinals(claims, self._store, default_as_of=self._captured_at)
                rendered = render_answer(
                    claims,
                    self._store,
                    ordinals=ordinals,
                    state=state,
                    incomplete_reasons=typed_reasons,
                )
                verdict = validate_analysis(
                    claims=claims,
                    facets=typed_facets,
                    store=self._store,
                    rendered=rendered,
                    scope=self._scope,
                    reauthorize=self._source.reauthorize,
                )
            if verdict.outcome is not CheckOutcome.PASS:
                raise SourceSerializationError("Recorded history validation is unavailable")
            evidence = []
            for claim in claims:
                for ref in claim.fact_refs:
                    fact = self._store.facts[ref]
                    evidence.append({
                        "source_type": fact.source_class,
                        "source_id": fact.source_id,
                        "source_revision": fact.source_revision,
                        "locator": {"field": fact.locator["field"], "page": None, "chunk": None},
                        "as_of": fact.as_of,
                        "authorization_class": fact.authorization_class,
                        "claim": claim.claim_id,
                    })
            body = {
                "kind": "evidence_analysis",
                "response_version": 2,
                "response_state": state,
                "detailed_response": rendered.detailed_response,
                "spoken_summary": "",
                "reasoning_summary": rendered.reasoning_summary,
                "evidence": evidence,
                "next_questions": [],
                "recommended_actions": [],
                "safety_boundary": "Recorded fixture observations only; no live status, engineering or release decision.",
                "speak": False,
                "payload": {
                    "payload_type": "evidence_analysis_v2",
                    "facets": facets,
                    "claims": [claim.model_dump(mode="json") for claim in claims],
                    "assumptions": [
                        "Only explicitly selected recorded fixture history is represented."
                    ],
                    "inclusion_rules": [],
                    "exclusion_rules": [],
                    "unknowns": [
                        "Full source population and revision completeness are not established."
                    ],
                },
                "incomplete_reasons": reasons,
            }
            response = CanonicalEvidenceAnalysisResponseV2.model_validate_json(
                _encode_bound(body, max_bytes=MAX_MODEL_BYTES)
            )
            if type(response) is not _CANONICAL_TYPE:
                raise SourceSerializationError("Recorded history canonical response is unavailable")
            result = json.loads(response.model_dump_json())
            if not _same(result, body):
                raise SourceSerializationError("Recorded history canonical response is unavailable")
        except MemoryError:
            raise
        except Exception:
            self._guard()
            raise SourceSerializationError("Recorded history rendering is unavailable") from None
        self._guard()
        return result

    def delivery_payload(self):
        """A distinct bounded history payload, not invented native population data.

        Existing UI/scope vocabularies are not widened. This internal payload's
        new kind is deliberately distinct from the ordinary evidence-analysis
        attachment, whose population-count contract cannot express unknowns.
        """
        self._guard()
        try:
            response = self.render()
            claims = [
                AnalysisClaim.model_validate_json(json.dumps(value))
                for value in response["payload"]["claims"]
            ]
            packet = {
                "kind": "recorded_history_analysis",
                "schema_version": "aimms.s6.history-analysis.v1",
                "canonical_response": response,
                "coverage": self._store.coverage_meta(),
                "citation_manifest": assign_ordinals(
                    claims, self._store, default_as_of=self._captured_at
                ).wire(),
            }
            result = json.loads(_encode_bound(packet, max_bytes=MAX_MODEL_BYTES))
        except MemoryError:
            raise
        except Exception:
            self._guard()
            raise SourceSerializationError("Recorded history delivery is unavailable") from None
        self._guard()
        return result

    async def run_stream(self, run_context):
        """Guard native event handoffs and the subsequent generator separately.

        The owning server supplies its native delivery context; this method
        neither establishes thread authority nor registers a production route.
        No shared emitter/cache is looked up and no operational effect is run.
        """
        if type(run_context) is not RunContext or not isinstance(run_context.emitter, EventEmitter):
            raise TypeError("A trusted native delivery context is required")
        emitter = run_context.emitter
        route = {
            "thread_id": run_context.thread_id,
            "run_id": run_context.run_id,
            "agent_name": run_context.agent_name,
        }
        source_guard = sync_to_async(self._guard, thread_sensitive=True)

        async def guard():
            await source_guard()
            if run_context.emitter is not emitter or not _same(
                {key: getattr(run_context, key) for key in route}, route
            ):
                raise HistoryAuthorizationError("Recorded history delivery is unavailable")

        packet = await sync_to_async(self.delivery_payload, thread_sensitive=True)()
        text = packet["canonical_response"]["detailed_response"]

        async def emit(kind, data):
            # Independent data copies bind fallible native event construction.
            try:
                encoded = _encode_bound(data, max_bytes=MAX_MODEL_BYTES)
                expected = json.loads(encoded)
                event = AGUIEvent(event_type=kind, data=json.loads(encoded), **route)
            except MemoryError:
                raise
            except Exception:
                await guard()
                raise SourceSerializationError("Recorded history event is unavailable") from None
            if (
                type(event) is not _EVENT_TYPE
                or event.event_type is not kind
                or not _same(event.data, expected)
                or not _same({key: getattr(event, key) for key in route}, route)
            ):
                await guard()
                raise SourceSerializationError("Recorded history event is unavailable")
            await guard()
            try:
                await emitter.emit(event)
            except MemoryError:
                raise
            except Exception:
                await guard()
                raise SourceSerializationError("Recorded history delivery is unavailable") from None
            await guard()

        await emit(EventType.STATE_DELTA, packet)
        message_id = uuid4().hex
        await emit(EventType.TEXT_MESSAGE_START, {"messageId": message_id, "role": "assistant"})
        await emit(EventType.TEXT_MESSAGE_CONTENT, {"messageId": message_id, "delta": text})
        await emit(EventType.TEXT_MESSAGE_END, {"messageId": message_id})
        # The awaited final event can revoke authority before this other channel.
        await guard()
        yield text


def read_recorded_history_analysis(
    *,
    environment,
    machine_pk,
    source_pk,
    binding_pk,
    window,
    adapter=None,
    max_samples_per_read=MAX_HISTORY_SAMPLES_PER_READ,
    max_samples_total=MAX_HISTORY_SAMPLES_TOTAL,
    max_reads=MAX_HISTORY_READS,
    split_recoverable_errors=False,
    max_elapsed_seconds=30,
):
    """Build real native evidence from the current ASGI/S1/S6 source path only."""
    scope = _selection(machine_pk)
    source = read_recorded_history_for_current_ai(
        environment=environment,
        machine_pk=machine_pk,
        source_pk=source_pk,
        binding_pk=binding_pk,
        window=window,
        adapter=adapter,
        max_samples_per_read=max_samples_per_read,
        max_samples_total=max_samples_total,
        max_reads=max_reads,
        split_recoverable_errors=split_recoverable_errors,
        max_elapsed_seconds=max_elapsed_seconds,
    )
    result = RecordedHistoryAnalysis(source, scope=scope, machine_pk=machine_pk)
    if result.state == "available":
        result._guard()
    return result
