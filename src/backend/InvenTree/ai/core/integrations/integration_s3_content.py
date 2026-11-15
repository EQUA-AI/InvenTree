"""Invocation-local S3 source to native analysis, never historical proof.

Only trusted server code selects the operation/environment/source. No tool,
route, model call, extraction service, shared cache or native action is added.
Real-policy historical withholding remains in the unchanged source readers.
"""

from __future__ import annotations

import hashlib
import json
from base64 import b64decode
from datetime import UTC, datetime
from uuid import uuid4

from ai.core.analysis.evidence import EvidenceStore, FactValue, TypedFact
from ai.core.analysis.renderer import assign_ordinals, render_answer, source_record_templates
from ai.core.analysis.schemas import (
    AnalysisClaim,
    AnalysisFacet,
    CanonicalEvidenceAnalysisResponseV2,
)
from ai.core.analysis.scope_context import TurnScopeContext, turn_scope_context
from ai.core.analysis.validator import CheckOutcome, validate_analysis
from ai.core.streaming import AGUIEvent, EventEmitter, EventType, RunContext
from asgiref.sync import sync_to_async
from assets.ai_read import fence
from tasks.integration_s3_reader import read_source_for_current_ai
from tasks.integration_s3_wire import SourceSerializationError

MAX_SOURCE_TEXT = 512
MAX_MODEL_BYTES = 64 * 1024
_TEMPLATE = "analysis.source_record_field"
_WORK_FIELDS = (
    "reference",
    "title",
    "description",
    "status",
    "lifecycle_status",
    "is_active",
    "priority",
    "due_date",
    "work_order_type",
    "created_at",
    "updated_at",
    "scheduled_start",
    "scheduled_end",
    "actual_started_at",
    "actual_completed_at",
    "estimated_minutes",
    "lifecycle_version",
    "hold_reason",
    "affected_component",
    "affected_component_ref",
)
_MAINTENANCE_FIELDS = ("date", "summary", "details", "created_at", "updated_at")
_DOCUMENT_FIELDS = (
    "kind",
    "filename",
    "external_link",
    "comment",
    "upload_date",
    "file_size",
    "is_image",
    "metadata_revision",
)
_DATE_FIELDS = frozenset({"due_date", "date", "upload_date"})
_TIME_FIELDS = frozenset({
    "created_at",
    "updated_at",
    "scheduled_start",
    "scheduled_end",
    "actual_started_at",
    "actual_completed_at",
})
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
_STORE_TYPE, _FACT_TYPE, _VALUE_TYPE = EvidenceStore, TypedFact, FactValue
_CANONICAL_TYPE = CanonicalEvidenceAnalysisResponseV2
_EVENT_TYPE = AGUIEvent


class SourceAuthorizationError(Exception):
    """Current invocation, selection or source authority is unavailable."""


class SourceCapabilityError(Exception):
    """The bounded source/model projection cannot represent this request."""


def _same(actual, expected):
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


def _encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


def _encode_bound(value):
    expected = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    payload = _encode(value)
    if (
        type(payload) is not bytes
        or len(payload) > MAX_MODEL_BYTES
        or not _same(json.loads(payload), expected)
    ):
        raise SourceSerializationError("S3 encoded projection is unavailable")
    return payload


def _selection():
    scope = turn_scope_context.get()
    if scope is not None:
        if type(scope) is not TurnScopeContext:
            raise SourceAuthorizationError("S3 selection is unavailable")
        if (
            scope.explicit
            or scope.date_from is not None
            or scope.date_to is not None
            or scope.source_classes
        ):
            # Direct S3 fields omit independently authorized historical machine
            # relationships. Never reconstruct them from current ownership.
            raise SourceCapabilityError("S3 selection is unsupported")
    return scope


def _value(name, raw):
    if raw is None:
        return ("text", None), "null"
    if type(raw) is bool:
        return ("bool", raw), "boolean"
    if type(raw) is int:
        return ("int", raw), "integer"
    if type(raw) is not str or len(raw) > MAX_SOURCE_TEXT:
        raise SourceCapabilityError("S3 source field projection is unsupported")
    if name in _DATE_FIELDS | _TIME_FIELDS:
        return (
            "date" if name in _DATE_FIELDS else "datetime",
            raw,
        ), "date" if name in _DATE_FIELDS else "datetime"
    quoted = json.dumps(raw, ensure_ascii=False)
    if len(quoted) > MAX_SOURCE_TEXT:
        raise SourceCapabilityError("S3 source field projection is unsupported")
    return ("text", fence(quoted, limit=MAX_SOURCE_TEXT)), "fenced_text"


def _content_fields(content):
    """Retain original bytes privately; expose only bounded literal UTF-8 text."""
    if (
        type(content) is not dict
        or set(content) != {"encoding", "data", "byte_size", "sha256"}
        or content["encoding"] != "base64"
        or type(content["data"]) is not str
        or type(content["byte_size"]) is not int
        or type(content["sha256"]) is not str
    ):
        raise SourceSerializationError("S3 content proof is unavailable")
    try:
        original = b64decode(content["data"], validate=True)
    except ValueError:
        raise SourceSerializationError("S3 content proof is unavailable") from None
    if (
        len(original) != content["byte_size"]
        or hashlib.sha256(original).hexdigest() != content["sha256"]
    ):
        raise SourceSerializationError("S3 content proof is unavailable")
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError:
        raise SourceCapabilityError("S3 binary content projection is unsupported") from None
    if any(not char.isprintable() and char not in "\t\r\n" for char in text):
        raise SourceCapabilityError("S3 binary content projection is unsupported")
    return {
        "content_text": text,
        "content_byte_size": content["byte_size"],
        "content_sha256": content["sha256"],
    }


def _source_fields(record, operation):
    """Map only actual declared direct fields; retain typed owner semantics."""
    if operation in ("work.core", "work.context", "maintenance.direct"):
        names, schema, source_class = (
            (_WORK_FIELDS, "aimms.work.core_details.v1", "work_order")
            if operation == "work.core"
            else (_MAINTENANCE_FIELDS, "aimms.maintenance.direct_details.v1", "maintenance_record")
        )
        if operation == "work.context":
            names = (*_WORK_FIELDS, "machine_state", "machine_pk", "machine_name", "machine_active")
            schema, source_class = "aimms.work.context.v1", "work_order"
        if (
            type(record) is not dict
            or set(record) != {"schema_version", "identity", "withheld_fields", "fields"}
            or record["schema_version"] != schema
            or type(record["fields"]) is not dict
            or set(record["fields"]) != set(names)
        ):
            raise SourceSerializationError("S3 direct source fields are unavailable")
        if operation == "work.context":
            values = record["fields"]
            state = values["machine_state"]
            if (
                state not in ("none", "unknown", "available")
                or (
                    state == "available"
                    and (
                        type(values["machine_pk"]) is not int
                        or values["machine_pk"] < 1
                        or type(values["machine_name"]) is not str
                        or type(values["machine_active"]) is not bool
                    )
                )
                or (
                    state != "available"
                    and any(
                        values[name] is not None
                        for name in ("machine_pk", "machine_name", "machine_active")
                    )
                )
            ):
                raise SourceSerializationError("S3 machine context is unavailable")
        return source_class, {name: record["fields"][name] for name in names}, None
    if operation not in (
        "work_document.metadata",
        "machine_document.metadata",
        "work_document.content",
        "machine_document.content",
    ):
        raise SourceCapabilityError("S3 operation projection is unsupported")
    content = operation.endswith(".content")
    if content and (
        type(record) is not dict
        or set(record) != {"schema_version", "metadata", "content"}
        or record["schema_version"] != "aimms.document.content.v1"
    ):
        raise SourceSerializationError("S3 content source is unavailable")
    metadata = record["metadata"] if content else record
    if (
        type(metadata) is not dict
        or set(metadata)
        != {"schema_version", "source_class", "identity", "owner", *_DOCUMENT_FIELDS}
        or metadata["schema_version"] != "aimms.document.metadata.v1"
        or metadata["source_class"] != "native_attachment"
        or type(metadata["owner"]) is not dict
        or set(metadata["owner"]) != {"domain", "environment", "pk"}
        or metadata["owner"]["domain"] != operation.split("_document.")[0]
        or type(metadata["owner"]["pk"]) is not int
        or metadata["owner"]["pk"] < 1
    ):
        raise SourceSerializationError("S3 document source is unavailable")
    fields = {
        "owner_domain": metadata["owner"]["domain"],
        "owner_id": metadata["owner"]["pk"],
        **{name: metadata[name] for name in _DOCUMENT_FIELDS},
    }
    revision = metadata["metadata_revision"]
    if content:
        fields.update(_content_fields(record["content"]))
        revision = record["content"]["sha256"]
    return "native_attachment", fields, revision


def _definitions(payload, operation, nonce, captured_at):
    body = json.loads(payload)
    if (
        type(body) is not dict
        or set(body) != {"wire_schema", "record"}
        or body["wire_schema"] != "aimms.s3.source-wire.v1"
    ):
        raise SourceSerializationError("S3 source envelope is unavailable")
    record = body["record"]
    source_class, fields, revision = _source_fields(record, operation)
    coverage = {
        "source_operation": operation,
        "source_schema": record["schema_version"],
        "complete_population": False,
        "revision_complete": False,
        "direct_record_only": True,
        "consumer_captured_at": captured_at,
    }
    common = {
        "kind": "record_field",
        "source_class": source_class,
        "source_id": "observation_" + nonce,
        "source_revision": revision if revision is not None else "observation_" + nonce,
        "retrieval_id": "ret_" + nonce,
        "as_of": captured_at,
        "authorization_class": "maintenance_authorized",
        "entity_refs": (),
        "machine_id": None,
        "controlled": False,
    }
    definitions = []
    for name, raw in fields.items():
        value, kind = _value(name, raw)
        definitions.append({
            **common,
            "locator": {"field": name},
            "values": {
                "field_name": ("enum", name),
                "field_type": ("enum", kind),
                "field_value": value,
            },
        })
    return definitions, coverage


class SourceRecordAnalysis:
    """Private native working evidence for exactly one authenticated invocation."""

    def __init__(self, source, operation, scope):
        self._source, self._operation, self._scope = source, operation, scope
        self._nonce = uuid4().hex
        self._captured_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._store = EvidenceStore()
        if self.state != "available":
            return
        definitions, coverage = self._specifications()
        for definition in definitions:
            self._store.add_fact(
                **{key: definition[key] for key in _FACT_FIELDS},
                values={
                    name: FactValue(kind, value)
                    for name, (kind, value) in definition["values"].items()
                },
            )
        self._store.set_primary_coverage(coverage)
        # The factory verifies this exact class/source/scope and runs the
        # complete guard before returning it. Every public projection also
        # guards entry and return; construction exposes no source packet.

    @property
    def state(self):
        return self._source.outcome.state

    def _specifications(self):
        return _definitions(
            self._source.outcome.payload, self._operation, self._nonce, self._captured_at
        )

    def _guard(self):
        if _selection() is not self._scope:
            raise SourceAuthorizationError("S3 selection is unavailable")
        if self.state == "unsupported":
            raise SourceCapabilityError("S3 source is unsupported")
        if self.state != "available":
            raise SourceAuthorizationError("S3 source is unavailable")
        # Private evidence is checked before the fresh source fence below.
        # _checked invokes this fence before and after each projection, and
        # delivery invokes it around every await. A second full native read
        # inside the same guard only duplicated those fences; no authority
        # cache, cross-await reuse or change to the native reader is needed.
        definitions, coverage = self._specifications()
        store = self._store
        if (
            type(store) is not _STORE_TYPE
            or type(store.facts) is not dict
            or tuple(store.facts) != tuple(f"fact_{i}" for i in range(1, len(definitions) + 1))
            or store.calculations
            or store.evidence_sets
            or store.envelopes
            or not _same(store.coverage_meta(), coverage)
        ):
            raise SourceSerializationError("S3 native evidence is unavailable")
        for index, definition in enumerate(definitions, 1):
            fact = store.facts[f"fact_{index}"]
            if (
                type(fact) is not _FACT_TYPE
                or type(fact.values) is not dict
                or not _same(fact.fact_id, f"fact_{index}")
                or not _same(
                    {key: getattr(fact, key) for key in _FACT_FIELDS},
                    {key: definition[key] for key in _FACT_FIELDS},
                )
                or fact.values.keys() != definition["values"].keys()
            ):
                raise SourceSerializationError("S3 native evidence is unavailable")
            for name, (kind, raw) in definition["values"].items():
                value = fact.values[name]
                if type(value) is not _VALUE_TYPE or not _same(
                    (value.value_type, value.raw, value.unit), (kind, raw, None)
                ):
                    raise SourceSerializationError("S3 native evidence is unavailable")
        if self._source.reauthorize() is not True:
            raise SourceAuthorizationError("S3 source is unavailable")

    def _checked(self, operation):
        self._guard()
        try:
            result = operation()
        except MemoryError:
            raise
        except Exception:
            self._guard()
            raise SourceSerializationError("S3 analysis projection is unavailable") from None
        self._guard()
        return result

    def synthesis_view(self):
        """Allowlist and bind the native model projection, never arbitrary DTOs."""

        def project():
            native = self._store.synthesis_view(template_keys=(_TEMPLATE,))
            rows = native["facts"]
            if type(rows) is not list or len(rows) != len(self._store.facts):
                raise SourceSerializationError("S3 native model facts are unavailable")
            facts = []
            for row, fact in zip(rows, self._store.facts.values(), strict=True):
                if type(row) is not dict:
                    raise SourceSerializationError("S3 native model facts are unavailable")
                values = row.get("values")
                if type(values) is not dict:
                    raise SourceSerializationError("S3 native model facts are unavailable")
                expected = {key: getattr(fact, key) for key in _VIEW_FIELDS}
                expected["entity_refs"] = list(fact.entity_refs)
                selected = {key: row[key] for key in _VIEW_FIELDS}
                selected["values"] = {key: values[key] for key in fact.values}
                expected["values"] = fact.rendered_values()
                if not _same(selected, expected):
                    raise SourceSerializationError("S3 native model facts are unavailable")
                facts.append(selected)
            return json.loads(
                _encode_bound({
                    "facts": facts,
                    "calculations": [],
                    "evidence_sets": [],
                    "coverage": self._store.coverage_meta(),
                    "render_templates": [_TEMPLATE],
                })
            )

        return self._checked(project)

    def render(self):
        """Guard the complete native canonical projection on entry and return."""
        return self._checked(self._render_payload)

    def _render_payload(self):
        """Private synchronous rendering; callers retain fresh outer fences."""
        claims = [
            AnalysisClaim.model_validate_json(
                json.dumps({
                    "claim_id": f"claim_{i}",
                    "claim_role": "answer",
                    "claim_type": "direct_source_fact",
                    "evidence_classification": "documented",
                    "fact_refs": [fact.fact_id],
                    "calculation_output_refs": [],
                    "evidence_refs": [],
                    "entity_refs": [],
                    "render_template": _TEMPLATE,
                    "paraphrase": "",
                })
            )
            for i, fact in enumerate(self._store.facts.values(), 1)
        ]
        facets = [
            {
                "name": "source_record",
                "status": "answered",
                "claim_ids": [c.claim_id for c in claims],
            }
        ]
        with source_record_templates():
            ordinals = assign_ordinals(claims, self._store, default_as_of=self._captured_at)
            rendered = render_answer(claims, self._store, ordinals=ordinals)
            verdict = validate_analysis(
                claims=claims,
                facets=[AnalysisFacet.model_validate_json(json.dumps(row)) for row in facets],
                store=self._store,
                rendered=rendered,
                scope=self._scope,
                reauthorize=self._source.reauthorize,
            )
        if verdict.outcome is not CheckOutcome.PASS:
            raise SourceSerializationError("S3 native validation is unavailable")
        evidence = [
            {
                "source_type": fact.source_class,
                "source_id": fact.source_id,
                "source_revision": fact.source_revision,
                "locator": {"field": fact.locator["field"], "page": None, "chunk": None},
                "as_of": fact.as_of,
                "authorization_class": fact.authorization_class,
                "claim": claim.claim_id,
            }
            for claim, fact in zip(claims, self._store.facts.values(), strict=True)
        ]
        body = {
            "kind": "evidence_analysis",
            "response_version": 2,
            "response_state": "complete",
            "detailed_response": rendered.detailed_response,
            "spoken_summary": "",
            "reasoning_summary": rendered.reasoning_summary,
            "evidence": evidence,
            "next_questions": [],
            "recommended_actions": [],
            "speak": False,
            "safety_boundary": "Selected direct source fields only; no procedure, approval or completeness decision.",
            "payload": {
                "payload_type": "evidence_analysis_v2",
                "facets": facets,
                "claims": [claim.model_dump(mode="json") for claim in claims],
                "assumptions": ["Only the selected directly authorized record is represented."],
                "inclusion_rules": [],
                "exclusion_rules": [],
                "unknowns": [
                    "Full source population, historical attribution and revision completeness are not certified by this consumer."
                ],
            },
            "incomplete_reasons": [],
        }
        canonical = CanonicalEvidenceAnalysisResponseV2.model_validate_json(_encode_bound(body))
        if type(canonical) is not _CANONICAL_TYPE:
            raise SourceSerializationError("S3 canonical response is unavailable")
        result = json.loads(canonical.model_dump_json())
        if not _same(result, body):
            raise SourceSerializationError("S3 canonical response is unavailable")
        return result

    def delivery_payload(self):
        """Return a distinct internal packet without inventing population totals."""

        def project():
            response = self._render_payload()
            claims = [
                AnalysisClaim.model_validate_json(json.dumps(row))
                for row in response["payload"]["claims"]
            ]
            return json.loads(
                _encode_bound({
                    "kind": "source_record_analysis",
                    "schema_version": "aimms.s3.source-analysis.v1",
                    "canonical_response": response,
                    "coverage": self._store.coverage_meta(),
                    "citation_manifest": assign_ordinals(
                        claims, self._store, default_as_of=self._captured_at
                    ).wire(),
                })
            )

        return self._checked(project)

    async def run_stream(self, run_context):
        """Guard actual native events and the separate later generator yield.

        This internal entry does not establish native thread access, enable a
        route or make sequential revocation checks atomic.
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
                raise SourceAuthorizationError("S3 delivery route is unavailable")

        packet = await sync_to_async(self.delivery_payload, thread_sensitive=True)()
        text = packet["canonical_response"]["detailed_response"]

        async def emit(kind, data):
            try:
                encoded = _encode_bound(data)
                expected = json.loads(encoded)
                event = AGUIEvent(event_type=kind, data=json.loads(encoded), **route)
            except MemoryError:
                raise
            except Exception:
                await guard()
                raise SourceSerializationError("S3 event is unavailable") from None
            if (
                type(event) is not _EVENT_TYPE
                or event.event_type is not kind
                or not _same(event.data, expected)
                or not _same({key: getattr(event, key) for key in route}, route)
            ):
                await guard()
                raise SourceSerializationError("S3 event is unavailable")
            await guard()
            try:
                await emitter.emit(event)
            except MemoryError:
                raise
            except Exception:
                await guard()
                raise SourceSerializationError("S3 delivery is unavailable") from None
            await guard()

        await emit(EventType.STATE_DELTA, packet)
        message_id = uuid4().hex
        await emit(EventType.TEXT_MESSAGE_START, {"messageId": message_id, "role": "assistant"})
        await emit(EventType.TEXT_MESSAGE_CONTENT, {"messageId": message_id, "delta": text})
        await emit(EventType.TEXT_MESSAGE_END, {"messageId": message_id})
        await guard()
        yield text


_ANALYSIS_TYPE = SourceRecordAnalysis


def read_source_analysis(*, environment, operation, source_pk, max_bytes=None):
    """Build native evidence through the actual current-AI S3 intake only."""
    scope = _selection()
    source = read_source_for_current_ai(
        environment=environment, operation=operation, source_pk=source_pk, max_bytes=max_bytes
    )

    def current():
        if (
            _selection() is not scope
            or source.outcome.state != "available"
            or source.reauthorize() is not True
        ):
            raise SourceAuthorizationError("S3 source is unavailable")

    try:
        result = SourceRecordAnalysis(source, operation, scope)
        if (
            type(result) is not _ANALYSIS_TYPE
            or result._source is not source
            or not _same(result._operation, operation)
            or result._scope is not scope
        ):
            raise SourceSerializationError("S3 native evidence is unavailable")
        if result.state == "available":
            result._guard()
    except MemoryError:
        raise
    except SourceCapabilityError:
        current()
        raise
    except Exception:
        current()
        raise SourceSerializationError("S3 native evidence is unavailable") from None
    return result
