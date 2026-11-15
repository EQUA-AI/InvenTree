"""Source-bound transcript projections; conversation access is not source access.

Only the live root's private capture mints references. Public observations and
stored digests are integrity/continuity data, never grants. Restore callbacks
supply server-selected sources, not authorization; native readers reauthorize
as the current caller. Sources stay out of unbound model recall/compaction.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

REF_KEY = "_aimms_source_ref"
PUBLIC_KEY = "source_observation"
UNAVAILABLE = "This source-backed answer is not available under the current access or source state."
_REF_SCHEMA = "aimms.source-transcript.v1"
_PUBLIC_SCHEMA = "aimms.source-observation.v1"
_KINDS = {"source_record_analysis", "recorded_history_analysis"}
_CAPTURE: ContextVar[SourceCapture | None] = ContextVar("aimms_source_capture", default=None)


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _payload_hash(value):
    if type(value) is str:
        value = value.encode()
    if type(value) is not bytes:
        raise ValueError("Source material is unavailable")
    return hashlib.sha256(value).hexdigest()


def coordinates(selection):
    """Detach only server-selected coordinates; no adapter object is serialized."""
    from ai.core.integrations.source_reads import ContentReadSelection, _selection_key

    _selection_key(selection)
    result = {"environment": selection.environment, "source_pk": selection.source_pk}
    if type(selection) is ContentReadSelection:
        return {
            **result,
            "kind": "content",
            "operation": selection.operation,
            "max_bytes": selection.max_bytes,
        }
    return {
        **result,
        "kind": "history",
        "machine_pk": selection.machine_pk,
        "binding_pk": selection.binding_pk,
        "start_ms": selection.window.start_ms,
        "end_ms": selection.window.end_ms,
    }


def _material_hash(selection, outcome):
    """Actor identity is checked afresh; it is not a physical sample revision."""
    from ai.core.integrations.source_reads import HistoryReadSelection

    if type(selection) is HistoryReadSelection:
        body = json.loads(outcome.payload)
        context = body.get("context")
        if type(context) is dict:
            context.pop("actor_pk", None)
        return _hash(body)
    return _payload_hash(outcome.payload)


def observation(packet):
    """Represent observed coverage without inventing a population or revision."""
    from ai.core.analysis.wire import SourceObservationPayload

    if type(packet) is not dict or packet.get("kind") not in _KINDS:
        raise ValueError("Source observation is unavailable")
    coverage = packet["coverage"]
    response = packet["canonical_response"]
    if (
        type(coverage) is not dict
        or coverage.get("complete_population") is not False
        or coverage.get("revision_complete") is not False
        or response.get("response_version") != 2
        or response.get("recommended_actions") != []
        or type(packet["citation_manifest"]) is not list
    ):
        raise ValueError("Source observation is unavailable")
    history = packet["kind"] == "recorded_history_analysis"
    public = {
        "schema_version": _PUBLIC_SCHEMA,
        "kind": "recorded_history" if history else "record",
        "population_count": None,
        "complete_population": False,
        "revision_complete": False,
        "returned_count": coverage["returned_count"] if history else 1,
        "captured_at": coverage["consumer_captured_at"],
        "recorded_only": history,
        "window": coverage["requested_window"] if history else None,
        "window_complete": coverage["source_window_complete"] if history else None,
        "gaps": coverage["gaps"] if history else [],
        "source_evidence_kind": coverage.get("source_evidence_kind"),
        "citations": copy.deepcopy(packet["citation_manifest"]),
    }
    for citation in public["citations"]:
        if type(citation.get("locator")) is dict:
            citation["locator"] = {
                "page": None,
                "section": None,
                "field": None,
                **citation["locator"],
            }
    return SourceObservationPayload.model_validate(public, strict=True).model_dump(mode="json")


@dataclass
class SourceCapture:
    """Invocation-local object, never accepted from metadata or model arguments."""

    packet: dict | None = None
    reference: dict | None = None
    live_check: object = field(default=None, repr=False)
    _expected: tuple[str, str] | None = field(default=None, init=False, repr=False)

    def record(self, packet, selection, outcome, live_check):
        """Bind exactly one real source result before its first emitted content."""
        public = observation(packet)
        detached = json.loads(_encoded(packet))
        reference = {
            "schema_version": _REF_SCHEMA,
            "coordinates": coordinates(selection),
            "material_sha256": _material_hash(selection, outcome),
            "content_sha256": _hash(packet["canonical_response"]["detailed_response"]),
            "observation_sha256": _hash(public),
            "canonical_sha256": _hash(packet["canonical_response"]),
        }
        if self.reference is not None and (self.reference != reference or self.packet != detached):
            raise ValueError("A source turn cannot combine different observations")
        self.packet, self.reference, self.live_check = detached, reference, live_check
        self._expected = (_hash(reference), _hash(detached))
        return public


@contextmanager
def bind_source_capture(capture):
    """Bind and always reset the private capture for one normalized turn."""
    token = _CAPTURE.set(capture)
    try:
        yield capture
    finally:
        _CAPTURE.reset(token)


def capture_packet(packet, selection, outcome, live_check):
    """Called only by the guarded prepared source; direct legacy callers stay valid."""
    public = observation(packet)
    sink = _CAPTURE.get()
    if sink is not None:
        if type(sink) is not SourceCapture:
            raise ValueError("Source capture is unavailable")
        public = sink.record(packet, selection, outcome, live_check)
    return public


async def live_canonical(run, canonical, *, required=False):
    """Check the original live boundary around finalization, never reuse it later."""
    sink = getattr(run, "extras", {}).get("source_capture")
    if type(sink) is not SourceCapture or sink._expected is None:
        if REF_KEY in canonical or PUBLIC_KEY in canonical:
            if required:
                from ai.core.integrations.source_reads import SourceReadError

                raise SourceReadError()
            return unavailable_canonical(canonical)
        return canonical
    try:

        def intact():
            reference, packet, expected = sink.reference, sink.packet, sink._expected
            if type(reference) is not dict or type(packet) is not dict or expected is None:
                return False
            return (
                expected == (_hash(reference), _hash(packet))
                and _hash(canonical.get(REF_KEY)) == expected[0]
                and _hash(canonical.get("message")) == reference["content_sha256"]
                and _hash(canonical.get(PUBLIC_KEY)) == reference["observation_sha256"]
                and _hash(canonical.get("canonical_response")) == reference["canonical_sha256"]
            )

        if not intact():
            raise ValueError("Source answer continuity is unavailable")
        if not callable(sink.live_check):
            raise ValueError("Live source boundary is unavailable")
        await sink.live_check()
        if not intact():
            raise ValueError("Source answer continuity is unavailable")
        return canonical
    except MemoryError:
        raise
    except Exception:
        if required:
            from ai.core.integrations.source_reads import SourceReadError

            raise SourceReadError() from None
        return unavailable_canonical(canonical)


async def reconcile_unavailable(run):
    """Replace an observed source bubble before a detected terminal refusal."""
    from ai.core.streaming import EventType
    from ai.core.turn.events import _event_from_record

    sink = getattr(run, "extras", {}).get("source_capture")
    if type(sink) is not SourceCapture or sink._expected is None or run.emitter is None:
        return
    safe = unavailable_canonical({"thread_id": run.thread.pk, "turn_id": run.turn.pk})
    for record in safe["events"]:
        if record["type"] != EventType.RUN_FINISHED.value:
            await run.emitter.emit(_event_from_record(record))


@dataclass
class ProjectionBudget:
    """One bounded page/export chunk; exhaustion withholds rather than truncates."""

    remaining: int = 8
    deadline: float = field(default_factory=lambda: time.monotonic() + 10)

    def spend(self):
        """No stored source body is served after its projection budget expires."""
        if self.remaining <= 0 or time.monotonic() >= self.deadline:
            return False
        self.remaining -= 1
        return True

    @property
    def expired(self):
        """Allow a page to defer the next unchecked row to a fresh request."""
        return time.monotonic() >= self.deadline


def source_message(message):
    """Identify protected source metadata without interpreting it as a grant."""
    metadata = message.metadata or {}
    return REF_KEY in metadata or PUBLIC_KEY in metadata


def projection_batches(messages):
    """Keep export projections bounded while making every source row reachable."""
    batch, sources = [], 0
    for message in messages:
        protected = source_message(message)
        if protected and sources == 1:
            yield batch
            batch, sources = [], 0
        batch.append(message)
        sources += int(protected)
    if batch:
        yield batch


def _actor(actor_id):
    from ai.core.integrations.retrieval_authority import fresh_role
    from django.contrib.auth import get_user_model

    actor = get_user_model().objects.filter(pk=actor_id, is_active=True).first()
    return actor if actor is not None and fresh_role(actor, "work_order") else None


def _restore(reference):
    from ai.core.integrations.source_reads import (
        SourceReadAdapter,
        _configured_adapter,
    )

    adapter = _configured_adapter()
    if type(adapter) is not SourceReadAdapter or adapter.restore is None:
        return None
    adapter.__post_init__()
    restore = adapter.restore
    selection = restore(copy.deepcopy(reference["coordinates"]))
    if (
        selection is None
        or _configured_adapter() is not adapter
        or adapter.restore is not restore
        or coordinates(selection) != reference["coordinates"]
    ):
        return None
    return adapter, restore, selection


def available(actor_id, reference, *, content, public, budget=None):
    """Read current native source under a fresh caller, not a fabricated request."""
    from ai.core.integrations.source_reads import ContentReadSelection, _configured_adapter

    budget = budget or ProjectionBudget()
    if not budget.spend():
        return False
    try:
        if (
            type(reference) is not dict
            or set(reference)
            != {
                "schema_version",
                "coordinates",
                "material_sha256",
                "content_sha256",
                "observation_sha256",
                "canonical_sha256",
            }
            or reference["schema_version"] != _REF_SCHEMA
            or _hash(content) != reference["content_sha256"]
            or _hash(public) != reference["observation_sha256"]
            or type(public) is not dict
            or public.get("schema_version") != _PUBLIC_SCHEMA
        ):
            return False
        restored = _restore(reference)
        if restored is None or _actor(actor_id) is None:
            return False
        adapter, restore, selection = restored

        def resolve():
            return _actor(actor_id)

        if type(selection) is ContentReadSelection:
            from tasks.integration_s3_reader import _read_source

            outcome = _read_source(
                resolve,
                environment=selection.environment,
                operation=selection.operation,
                source_pk=selection.source_pk,
                max_bytes=selection.max_bytes,
            )
        else:
            from machine_health.services.integration_s6_reader import (
                MAX_HISTORY_READS,
                MAX_HISTORY_SAMPLES_PER_READ,
                MAX_HISTORY_SAMPLES_TOTAL,
                _read_recorded_history,
            )

            outcome = _read_recorded_history(
                resolve,
                environment=selection.environment,
                machine_pk=selection.machine_pk,
                source_pk=selection.source_pk,
                binding_pk=selection.binding_pk,
                window=selection.window,
                adapter=selection.adapter,
                max_samples_per_read=MAX_HISTORY_SAMPLES_PER_READ,
                max_samples_total=MAX_HISTORY_SAMPLES_TOTAL,
                max_reads=MAX_HISTORY_READS,
                split_recoverable_errors=False,
                max_elapsed_seconds=max(0.001, min(5, budget.deadline - time.monotonic())),
            )
        return (
            outcome.state == "available"
            and _material_hash(selection, outcome) == reference["material_sha256"]
            and _actor(actor_id) is not None
            and _configured_adapter() is adapter
            and adapter.restore is restore
            and coordinates(selection) == reference["coordinates"]
            and time.monotonic() < budget.deadline
        )
    except MemoryError:
        raise
    except Exception:
        return False


def project_message(message, actor_id, *, budget=None):
    """Return a detached, current-source-authorized message, never rewrite audit."""
    metadata = message.metadata or {}
    if REF_KEY not in metadata and PUBLIC_KEY not in metadata:
        return message
    result = copy.copy(message)
    result.metadata = copy.deepcopy(metadata)
    if not available(
        actor_id,
        metadata.get(REF_KEY),
        content=message.content,
        public=metadata.get(PUBLIC_KEY),
        budget=budget,
    ):
        result.content = UNAVAILABLE
        result.metadata = {
            "source_observation": {"schema_version": _PUBLIC_SCHEMA, "state": "unavailable"}
        }
    return result


def project_canonical(canonical, actor_id, *, budget=None):
    """Current source checks protect idempotency replay, including stored events."""
    if REF_KEY not in canonical and PUBLIC_KEY not in canonical:
        return canonical
    reference = canonical.get(REF_KEY)
    try:
        intact = (
            type(reference) is dict
            and reference.get("canonical_sha256") == _hash(canonical.get("canonical_response"))
            and canonical.get("_source_events_sha256") == _hash(canonical.get("events", []))
        )
        readable = intact and available(
            actor_id,
            reference,
            content=canonical.get("message"),
            public=canonical.get(PUBLIC_KEY),
            budget=budget,
        )
    except (TypeError, ValueError):
        readable = False
    if readable:
        return canonical
    return unavailable_canonical(canonical)


def unavailable_canonical(canonical):
    """No original content/labels/events survive an unavailable stored read."""
    from ai.core.streaming import AGUIEvent, EventType
    from ai.core.turn.responses import _canonical_terminal_response

    result = {k: canonical[k] for k in ("thread_id", "turn_id") if k in canonical}
    response = _canonical_terminal_response("incomplete", UNAVAILABLE)
    result.update(
        message=UNAVAILABLE,
        canonical_response=response.model_dump(mode="json"),
        response_state="incomplete",
        spoken_summary="",
        agent="source_unavailable",
        workflow_used="source_unavailable",
        route=None,
    )
    result[PUBLIC_KEY] = {"schema_version": _PUBLIC_SCHEMA, "state": "unavailable"}
    result["events"] = [
        AGUIEvent(event_type=kind, thread_id=str(result.get("thread_id", "")), data=data).to_dict()
        for kind, data in (
            (
                EventType.STATE_DELTA,
                {"kind": "source_observation", PUBLIC_KEY: result[PUBLIC_KEY]},
            ),
            (
                EventType.MESSAGES_SNAPSHOT,
                {
                    "messages": [
                        {"id": "source-unavailable", "role": "assistant", "content": UNAVAILABLE}
                    ]
                },
            ),
            (EventType.RUN_FINISHED, {"status": "incomplete"}),
        )
    ]
    return result
