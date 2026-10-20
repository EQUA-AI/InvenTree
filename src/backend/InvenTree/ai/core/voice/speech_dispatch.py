"""One application speech-dispatch coordinator per voice session (plan §5.3).

Not a second conversational agent: this is the single owner of exact-TTS
dispatch around the existing provider channel. Everything spoken is
persisted first and dispatched as EXACT text whose hash matches the
persisted row (``ai.core.voice.speech``); this module never invents,
paraphrases, or streams speech.

What it owns per dispatch: the client event id, the provider response id
(learned on acknowledgement), the persisted utterance id, the utterance
kind (answer/status/prompt), the session generation and the lifecycle
state. Lifecycle mapping is honest: a successful send is ``REQUESTED``
(requested playback -- NOT heard), ``response.done`` is ``COMPLETED`` only
from the provider's own correlated terminal status and only its explicit
``completed`` status (an absent, malformed or unknown status is an honest
``FAILED``, never a false completion), ``response.cancelled`` (or a
provider-cancelled ``response.done``) is ``CANCELED``, and a
rejected/failed/uncertain dispatch is ``FAILED``. An interrupted dispatch
is never completed.

Provider ownership is tracked SEPARATELY from local playback state: a
locally canceled dispatch may still own an outstanding ``response.create``
or an acknowledged response at the provider. The single owner (``_owner``)
is reconciled -- create -> cancel -> actual terminal -- by the next
replacement under hard bounds, or speech fails CLOSED. Generation
invalidation (barge-in/thread change) stops local playback immediately but
still reconciles that ownership; its late events can never speak, only
release.

Replacement rules (§5.3.2-5.3.4):

* final answers take priority over optional status; optional status never
  interrupts an in-flight answer or prompt;
* a not-yet-sent filler is cancelled immediately (no provider traffic);
* a dispatched filler awaiting acknowledgement is reconciled -- await the
  create acknowledgement, cancel, await the terminal event -- under a hard
  bound;
* on bound failure speech fails CLOSED (safe text only: the channel is
  reset/torn down, later speech stays text-only) -- never two competing
  speech requests, never a replay of the business turn to obtain speech.

Correlation limitation (documented, deliberate): provider ``response.created``
events do not echo client event ids, so a response is attributed to the
single outstanding ``response.create`` owner only. The coordinator
guarantees one outstanding create per session; the event gate refuses to
guess across several (no multi-pending FIFO attribution). Terminal events
require the acknowledged owner identity: stale, foreign or missing response
ids never terminate another dispatch, and a malformed (id-less)
acknowledgement binds nothing.

Cancellation landing around the WebSocket send side effect is handled at
the gateway: a definite failed send releases its allowance, while a
cancelled send (side effect unknown) reconciles/closes the channel. Either
way no allowance may legitimize a later autonomous response. Every wait
this module owns is bounded: the WebSocket send, the replacement
ack/cancel/terminal waits and the teardown/playback-audit callbacks (a
failed or hung audit write is dropped, never retried). On an uncertain send
side effect or a bound timeout speech fails CLOSED (text fallback) --
never an overlap and never a retried business operation.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
import typing
import uuid
from dataclasses import dataclass, field
from typing import Literal

from ai.core.voice import timing
from ai.core.voice.speech import build_exact_tts_payload

SpeechKind = Literal["answer", "status", "prompt"]
DispatchState = Literal["pending", "requested", "completed", "canceled", "failed"]
ProviderState = Literal["none", "create_sent", "response_active", "released"]

PENDING: DispatchState = "pending"
REQUESTED: DispatchState = "requested"
COMPLETED: DispatchState = "completed"
CANCELED: DispatchState = "canceled"
FAILED: DispatchState = "failed"

PROVIDER_NONE: ProviderState = "none"
PROVIDER_CREATE_SENT: ProviderState = "create_sent"
PROVIDER_RESPONSE_ACTIVE: ProviderState = "response_active"
PROVIDER_RELEASED: ProviderState = "released"

#: Dispatch state -> persisted playback state. ``COMPLETED`` is the model's
#: ``done``: it means the provider reported terminal completion -- still not
#: proof of heard audio (send does not mean heard).
PLAYBACK_STATE_FOR: dict[str, str] = {
    "pending": "pending",
    "requested": "requested",
    "completed": "done",
    "canceled": "canceled",
    "failed": "failed",
}

TERMINAL_STATES = frozenset({COMPLETED, CANCELED, FAILED})

#: Bounded replacement waits (policy values, tunable after measurement).
REPLACE_TIMEOUT_S = 2.0
CANCEL_TERMINAL_TIMEOUT_S = 2.0
#: Bounded WebSocket send: an indefinite send must never hang a turn.
SEND_TIMEOUT_S = 5.0
#: Bounded teardown callbacks: closing speech must always complete.
CLOSE_TIMEOUT_S = 2.0


class SpeechDispatchError(RuntimeError):
    """Stable dispatch failure code; never carries content."""

    code = "VOICE_SPEECH_DISPATCH_FAILED"


@dataclass
class DispatchRecord:
    """Content-free dispatch ledger entry (ids/kind/state only)."""

    dispatch_id: str
    utterance_id: str
    kind: SpeechKind
    generation: int
    state: DispatchState = PENDING
    reason: str = ""
    client_event_id: str = ""
    response_id: str | None = None
    created_monotonic: float = 0.0
    ack_monotonic: float | None = None
    terminal_monotonic: float | None = None
    send_started: bool = False
    #: Provider-side ownership, independent of local playback state: a
    #: locally canceled dispatch may still own an outstanding create or an
    #: acknowledged response that must be reconciled before any replacement.
    provider_state: ProviderState = PROVIDER_NONE
    #: Whether a ``response.cancel`` was already sent for this dispatch
    #: (never send competing duplicates).
    cancel_sent: bool = False
    #: The request-local measurement bound at dispatch time: event-driven
    #: durations land here even when provider events arrive in another async
    #: context (the gateway drain), never on a None/wrong-turn capture.
    _measurement: typing.Any = field(default=None, repr=False)
    _ack_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _terminal_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _released_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _settled: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    #: Durable audit outcome is separate from the provider/audio lifecycle.
    playback_audit_state: typing.Literal["unavailable", "confirmed", "uncertain"] = "unavailable"
    _playback_audit_sequence: int = field(default=0, repr=False)


class SessionSpeechDispatcher:
    """The one exact-speech owner for one voice session."""

    def __init__(
        self,
        session_id: str,
        channel: typing.Any,
        *,
        clock: typing.Callable[[], float] = time.perf_counter,
        on_playback: typing.Callable[..., typing.Any] | None = None,
        on_close_channel: typing.Callable[..., typing.Any] | None = None,
        replace_timeout_s: float = REPLACE_TIMEOUT_S,
        cancel_timeout_s: float = CANCEL_TERMINAL_TIMEOUT_S,
        send_timeout_s: float = SEND_TIMEOUT_S,
        close_timeout_s: float = CLOSE_TIMEOUT_S,
    ) -> None:
        self.session_id = str(session_id)
        self.channel = channel
        self._clock = clock
        self._on_playback = on_playback
        self._on_close_channel = on_close_channel
        self._replace_timeout_s = replace_timeout_s
        self._cancel_timeout_s = cancel_timeout_s
        self._send_timeout_s = send_timeout_s
        self._close_timeout_s = close_timeout_s
        self._lock = asyncio.Lock()
        self._current: DispatchRecord | None = None
        #: The single record whose provider create/response is outstanding.
        #: Enforces one outstanding create with a known owner; the event gate
        #: refuses to guess across several (no multi-pending FIFO guessing).
        self._owner: DispatchRecord | None = None
        self._generation = 0
        self._closed = False
        #: Response ids already bound to a dispatch's lifecycle. A provider
        #: acknowledgement repeating one of these is historical/duplicate --
        #: the event gate may still allow its lifecycle, but it is NEVER a new
        #: owner's identity (binding it would let its stale terminal finish
        #: the wrong dispatch and strand the real create's allowance).
        self._retired_response_ids: set[str] = set()

    @property
    def generation(self) -> int:
        """The current output generation (content-free telemetry/audit)."""
        return self._generation

    @property
    def closed(self) -> bool:
        """Whether speech has failed closed / the session ended."""
        return self._closed

    # -- public API (one entry for prompt/status/answer; cancel/close for
    #    delete/end) --------------------------------------------------- #
    async def speak(
        self,
        *,
        kind: SpeechKind,
        text: str,
        spoken_hash: str,
        utterance_id: str,
        onset_kind: typing.Literal["filler", "answer"] | None = None,
        rate: float | None = None,
        required: bool = False,
    ) -> DispatchRecord:
        """Dispatch one EXACT persisted utterance.

        Never raises on transport failure (the record reports ``failed``);
        raises only when the persist-before-speak contract is violated or a
        caller passes a non-allow-listed rate/onset (validated before any
        provider traffic).

        ``onset_kind`` is derived by the caller from the persisted row and the
        application stage -- ``"filler"`` only for actual optional waiting,
        ``"answer"`` only for a completed-answer dispatch, absent otherwise.
        ``required`` marks a required outcome (error / answer-in-chat /
        incomplete / clarification / readback): only OPTIONAL status speech is
        ever suppressed behind live answer/prompt speech; a required outcome
        is never dropped merely because its dispatch class is ``status``.
        """
        if kind not in ("answer", "status", "prompt"):
            raise ValueError("unknown speech kind")
        # Exact-speech validation BEFORE any provider traffic.
        payload = build_exact_tts_payload(
            persisted_text=text,
            persisted_hash=spoken_hash,
            utterance_id=utterance_id,
            speech_kind=kind,
            onset_kind=onset_kind,
            dispatch_generation=str(self._generation),
            rate=rate,
        )
        record = DispatchRecord(
            dispatch_id=uuid.uuid4().hex,
            utterance_id=str(utterance_id),
            kind=kind,
            generation=self._generation,
            created_monotonic=self._clock(),
        )
        measurement = timing.capture_measurement()
        if measurement is not None:
            measurement.bind(utterance_id=str(utterance_id), kind=kind, generation=self._generation)
        record._measurement = measurement
        async with self._lock:
            if self._closed:
                return await self._finish(record, FAILED, "channel_closed")
            prior = self._current
            live = prior if (prior is not None and prior.state not in TERMINAL_STATES) else None
            if live is not None and kind == "status" and not required and live.kind != "status":
                # Optional waiting speech NEVER interrupts an answer or an
                # explicit prompt (plan §5.2: queued status must not
                # interrupt the previous turn's speech). Required outcomes
                # pass ``required=True`` and are never dropped this way.
                return await self._finish(record, CANCELED, "suppressed_behind_answer")
            # Provider ownership is reconciled even when the prior playback
            # is already locally terminal (cancel/barge-in): the old create
            # or acknowledged response must reach its actual terminal state
            # before any replacement, under bound -- or speech fails closed.
            # (Invariant: a live sent record IS the owner; a live pre-send
            # record carries no provider ownership.)
            target = live if live is not None else self._owner
            if target is not None:
                if not await self._replace(target):
                    # Fail speech CLOSED: safe text only.
                    return await self._finish(record, FAILED, "speech_closed")
                # Re-fence AFTER the bounded replacement waits: close or a
                # generation invalidation may have landed while this dispatch
                # waited under the lock. A refused (closed) or invalidated
                # generation dispatch must never reach the send side effect.
                if self._closed:
                    return await self._finish(record, FAILED, "channel_closed")
                if record.generation != self._generation:
                    return await self._finish(record, CANCELED, "invalidated_before_send")
            self._current = record
        # Cooperative point: a cancellation may land before the send side
        # effect; it cancels this record without any provider traffic.
        await asyncio.sleep(0)
        if record.state != PENDING:
            return record
        fence = self._pre_send_fence(record)
        if fence is not None:
            return await self._finish(record, *fence)
        record.client_event_id = f"app-{uuid.uuid4().hex}"
        payload["event_id"] = record.client_event_id
        record.send_started = True
        # Ownership begins with the send ATTEMPT: the side effect may land
        # any time after the call starts, so acknowledgement and terminal
        # events must be correlatable while this send still awaits.
        record.provider_state = PROVIDER_CREATE_SENT
        self._owner = record
        try:
            await asyncio.wait_for(self.channel.send_control(payload), self._send_timeout_s)
        except asyncio.CancelledError:
            # The side effect may have landed: reconcile/close, never leave
            # an allowance that could legitimize a later response.
            record._settled.set()
            self._release(record)
            await self._finish(record, FAILED, "dispatch_canceled")
            await self._close_channel("dispatch_canceled")
            self._closed = True
            raise
        except TimeoutError:
            # Uncertain send side effect: fail speech CLOSED (text fallback);
            # never overlap and never retry the business operation.
            record._settled.set()
            self._release(record)
            await self._finish(record, FAILED, "send_bound_exceeded")
            await self._fail_closed("send_bound_exceeded")
            return record
        except Exception:
            # Definitive send failure: release only this ownership.
            record._settled.set()
            self._release(record)
            return await self._finish(record, FAILED, "send_failed")
        record._settled.set()
        # A terminal event may already have settled this record while the
        # send awaited; never regress a terminal state back to requested.
        return await self._finish(record, REQUESTED)

    async def cancel(
        self, *, reason: str = "canceled", kind: SpeechKind | None = None
    ) -> DispatchRecord | None:
        """Stop playback locally (barge-in/end/hidden/result-ready).

        Local stop is immediate and honest: the record becomes CANCELED
        (interrupted is never completed). Provider ownership is NOT dropped:
        when dispatch actually began, one best-effort ``response.cancel``
        (bounded, never duplicated) reaches the provider, and the
        outstanding create/response lifecycle is reconciled by the next
        replacement under bound -- or speech fails closed.
        """
        # Lock-independent immediate local stop (RV03): the shared lock may
        # be held by a replacement awaiting a provider terminal, and local
        # playback must stop before that terminal and before any persistence
        # barrier. The claim below (terminal state + ``cancel_sent``
        # reservation) lands synchronously before the first yield, so a
        # concurrent ``_replace`` or a second cancel claim can never send a
        # duplicate ``response.cancel``.
        record = self._current
        if record is None or record.state in TERMINAL_STATES:
            return None
        if kind is not None and record.kind != kind:
            return None
        send_cancel = await self._cancel_record_locked(record, reason)
        await self._send_response_cancel(record, send_cancel)
        return record

    def invalidate(self, reason: str = "barge_in") -> None:
        """Barge-in/thread change: new generation; old speech never speaks.

        Local playback stops immediately for every in-flight dispatch of the
        old generation, but their provider ownership is still reconciled
        (never ignored) so the next dispatch cannot overlap an old
        create/response.
        """
        self._generation += 1
        record = self._current
        if record is not None and record.state not in TERMINAL_STATES:
            with contextlib.suppress(RuntimeError):  # No running loop to cancel on.
                # Capture THIS record: the deferred cancellation targets the
                # captured old-generation dispatch and can never cancel a
                # fresh generation record registered later.
                asyncio.get_running_loop().create_task(self._cancel_record(record, reason))

    async def close(self, reason: str = "ended") -> None:
        """End of session: refuse new sends BEFORE any await, then cancel."""
        self._closed = True
        self._generation += 1
        await self.cancel(reason=reason)

    async def on_provider_event(self, event: dict, kind: str | None = None) -> str:
        """Correlate one classified provider event to the owned dispatch.

        Returns a content-free outcome code. Attribution is by the single
        outstanding owner and its acknowledged identity only: stale, foreign
        or missing ids are ignored -- never guessed. Events of a locally
        terminal (e.g. interrupted) dispatch only reconcile/release its
        provider ownership: interrupted is never completed and late events
        can no longer speak.
        """
        del kind  # classification is the gateway's; correlation is ours
        record = self._owner
        if record is None:
            return "ignored"
        event_type = str(event.get("type") or "")
        response_id = str(
            (event.get("response") or {}).get("id", "") or event.get("response_id") or ""
        )
        if event_type == "response.created":
            if record.provider_state != PROVIDER_CREATE_SENT:
                return "ignored"
            if not response_id:
                # Malformed acknowledgement: unadoptable. It binds nothing
                # and consumes no allowance; the bounded wait fails closed
                # if no real acknowledgement ever comes.
                return "malformed_ignored"
            if response_id in self._retired_response_ids:
                # Historical/duplicate acknowledgement: this id already
                # belongs to an earlier dispatch's lifecycle. The gate's
                # allowed historical lifecycle is not the new owner; the
                # real acknowledgement is still awaited under bound.
                return "stale_ack_ignored"
            record.response_id = response_id
            record.provider_state = PROVIDER_RESPONSE_ACTIVE
            self._retired_response_ids.add(response_id)
            record.ack_monotonic = self._clock()
            record._ack_event.set()
            if record.created_monotonic:
                timing.record_duration(
                    "dispatch_ack",
                    (record.ack_monotonic - record.created_monotonic) * 1000,
                    measurement=record._measurement,
                )
            return "ack"
        if event_type in ("response.done", "response.cancelled"):
            if record.response_id is None or not response_id or response_id != record.response_id:
                # Require the acknowledged owner identity: a stale terminal
                # for any historical/expected id, a foreign id or a missing
                # id never terminates another record.
                return "ignored"
            if event_type == "response.cancelled":
                return await self._terminate(record, CANCELED, "canceled")
            # ``response.done`` is not blanket completion: map the actual
            # correlated terminal status and never claim an interrupted or
            # failed response was heard. ONLY a positively known ``completed``
            # is completion; an absent, malformed or unknown status is not
            # proof of completion -- an honest failed record, never a false
            # COMPLETED (the provider vocabulary is not ours to invent).
            status = str((event.get("response") or {}).get("status") or "").lower()
            if status in ("cancelled", "canceled"):
                return await self._terminate(record, CANCELED, "provider_cancelled")
            if status in ("failed", "incomplete"):
                return await self._terminate(record, FAILED, "provider_failed")
            if status == "completed":
                return await self._terminate(record, COMPLETED, "completed")
            return await self._terminate(record, FAILED, "provider_status_unknown")
        if event_type == "error":
            error = event.get("error") or {}
            attribution = str(error.get("event_id") or error.get("response_id") or "")
            if not attribution:
                # A stale response.cancel error and other unattributable
                # errors must never consume this dispatch.
                return "ignored"
            if attribution not in (record.client_event_id, record.response_id or ""):
                return "ignored"
            return await self._terminate(record, FAILED, "provider_error")
        return "ignored"

    def snapshot(self) -> dict:
        """Content-free dispatch state for telemetry/audit."""
        record = self._current
        return {
            "session_id": self.session_id,
            "generation": self._generation,
            "closed": self._closed,
            "state": record.state if record else None,
            "kind": record.kind if record else None,
            "dispatch_id": record.dispatch_id if record else None,
            "utterance_id": record.utterance_id if record else None,
            "playback_state": PLAYBACK_STATE_FOR.get(record.state) if record else None,
            "playback_audit_state": record.playback_audit_state if record else "unavailable",
        }

    # -- internals ------------------------------------------------------- #
    def _pre_send_fence(self, record: DispatchRecord) -> tuple[DispatchState, str] | None:
        """Final gate immediately before the send side effect.

        The cooperative point already let local cancellation land; this gate
        catches what cancellation alone cannot express: close's refusal and
        generation invalidation. A closed, invalidated or superseded dispatch
        must never produce provider traffic.
        """
        if self._closed:
            return FAILED, "channel_closed"
        if record.generation != self._generation:
            return CANCELED, "invalidated_before_send"
        if self._current is not record:
            return CANCELED, "superseded_before_send"
        return None

    async def _cancel_record(self, record: DispatchRecord, reason: str) -> None:
        """Cancel ONE captured record (invalidate targets the old generation)."""
        # Same lock-independent immediate local stop as ``cancel``: the
        # captured record is claimed synchronously (terminal state +
        # ``cancel_sent`` reservation) before any await yields.
        if record.state in TERMINAL_STATES:
            return
        send_cancel = await self._cancel_record_locked(record, reason)
        await self._send_response_cancel(record, send_cancel)

    async def _cancel_record_locked(self, record: DispatchRecord, reason: str) -> bool:
        """Immediate local stop without waiting on the shared provider
        replacement lock; returns whether the provider needs one best-effort
        ``response.cancel`` (one, never duplicated).

        The local terminal state and the ``cancel_sent`` reservation are
        claimed synchronously -- before the first await yields -- so
        ``_replace`` and any second cancel claim observe that reservation
        across awaits and can never duplicate the provider send. Provider
        ownership itself is NOT dropped here: it is reconciled by the
        replacement/terminal path exactly as before.
        """
        send_cancel = (
            record.send_started
            and record.provider_state in (PROVIDER_CREATE_SENT, PROVIDER_RESPONSE_ACTIVE)
            and not record.cancel_sent
        )
        if send_cancel:
            record.cancel_sent = True
        await self._finish(record, CANCELED, reason)
        # Ownership fence AT the awaited playback-callback boundary: the
        # untargeted ``response.cancel`` serves THIS captured record's
        # outstanding create/response only. A real terminal may have released
        # that provider ownership (and a fresh dispatch may now own the
        # provider) while the callback awaited; the captured-record check runs
        # after the await -- never ``self._current`` -- so this stale
        # continuation can never send control traffic against a newer owner.
        # The synchronous ``cancel_sent`` reservation above is unchanged and
        # still forbids any duplicate send.
        if self._owner is not record or record.provider_state not in (
            PROVIDER_CREATE_SENT,
            PROVIDER_RESPONSE_ACTIVE,
        ):
            return False
        return send_cancel

    async def _send_response_cancel(self, record: DispatchRecord, send_cancel: bool) -> None:
        if not send_cancel:
            return
        record.cancel_sent = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                self.channel.send_control({"type": "response.cancel"}),
                self._send_timeout_s,
            )

    def _release(self, record: DispatchRecord) -> None:
        """The record's provider ownership is gone (terminal/failed/torn down)."""
        record.provider_state = PROVIDER_RELEASED
        record._released_event.set()
        if self._owner is record:
            self._owner = None

    async def _replace(self, prior: DispatchRecord) -> bool:
        """Reconcile outstanding provider ownership before a replacement.

        Local playback may already be terminal (cancel/barge-in); the
        provider create/response lifecycle is reconciled regardless --
        create -> cancel -> actual terminal -- every wait bounded. On bound
        failure speech fails CLOSED (never two competing speech requests).
        """
        if prior.state == PENDING and not prior.send_started:
            # Not yet sent: cancel immediately, no provider traffic.
            await self._finish(prior, CANCELED, "replaced_before_send")
            return True
        if not await self._wait(prior._settled, self._replace_timeout_s):
            if prior.state not in TERMINAL_STATES:
                await self._finish(prior, FAILED, "send_bound_exceeded")
            return await self._fail_closed("send_bound_exceeded")
        if prior.provider_state == PROVIDER_RELEASED:
            return True
        if prior.provider_state == PROVIDER_CREATE_SENT:
            if not await self._wait(prior._ack_event, self._replace_timeout_s):
                if prior.state not in TERMINAL_STATES:
                    await self._finish(prior, FAILED, "ack_bound_exceeded")
                return await self._fail_closed("ack_bound_exceeded")
            if prior.provider_state == PROVIDER_RELEASED:
                return True
        if not prior.cancel_sent:
            prior.cancel_sent = True
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    self.channel.send_control({"type": "response.cancel"}),
                    self._send_timeout_s,
                )
        started = self._clock()
        if not await self._wait(prior._released_event, self._cancel_timeout_s):
            if prior.state not in TERMINAL_STATES:
                await self._finish(prior, FAILED, "cancel_bound_exceeded")
            return await self._fail_closed("cancel_bound_exceeded")
        timing.record_duration(
            "cancel_terminal",
            (self._clock() - started) * 1000,
            measurement=prior._measurement,
        )
        return True

    async def _wait(self, event: asyncio.Event, timeout: float) -> bool:
        try:
            await asyncio.wait_for(event.wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def _fail_closed(self, reason: str) -> bool:
        """Speech fails CLOSED: safe text only; never overlap or replay."""
        self._closed = True
        await self._close_channel(reason)
        return False

    async def _close_channel(self, reason: str) -> None:
        """Reset/teardown the provider channel so nothing stale survives.

        Every teardown wait is bounded: closing speech must always complete.
        """
        if self._on_close_channel is not None:
            with contextlib.suppress(Exception):
                result = self._on_close_channel(reason)
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, self._close_timeout_s)
        close = getattr(self.channel, "close", None)
        if close is not None:
            with contextlib.suppress(Exception):
                result = close()
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, self._close_timeout_s)

    async def _terminate(self, record: DispatchRecord, state: DispatchState, reason: str) -> str:
        # The provider lifecycle reached its terminal event: ownership is
        # released even when local playback already settled (interrupted is
        # never completed; a late completion never resurrects a cancel).
        self._release(record)
        if record.state in TERMINAL_STATES:
            return "late_ignored"
        record.terminal_monotonic = self._clock()
        record.state = state
        record.reason = reason
        record._terminal_event.set()
        if record.created_monotonic:
            name = "cancel_terminal" if state == CANCELED else "dispatch_terminal"
            timing.record_duration(
                name,
                (record.terminal_monotonic - record.created_monotonic) * 1000,
                measurement=record._measurement,
            )
        await self._emit(record)
        return state

    async def _finish(
        self, record: DispatchRecord, state: DispatchState, reason: str = ""
    ) -> DispatchRecord:
        if record.state not in TERMINAL_STATES:
            record.state = state
            if reason:
                record.reason = reason
            if state in TERMINAL_STATES:
                record.terminal_monotonic = self._clock()
                record._terminal_event.set()
            await self._emit(record)
        return record

    async def _emit(self, record: DispatchRecord) -> None:
        """Reconcile audit once without relabelling audio truth or replaying work."""
        if self._on_playback is None or not record.utterance_id:
            return
        playback_state = PLAYBACK_STATE_FOR.get(record.state)
        if playback_state is None:
            return
        record._playback_audit_sequence += 1
        sequence = record._playback_audit_sequence
        # An attempted write is unconfirmed until its own result settles. A
        # canceled/older callback must never overwrite a newer terminal outcome.
        record.playback_audit_state = "uncertain"
        audit_state = "uncertain"
        try:
            result = self._on_playback(record.utterance_id, playback_state)
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, self._close_timeout_s)
            if result is None:
                audit_state = "unavailable"
            elif isinstance(result, str) and result in ("confirmed", "uncertain", "unavailable"):
                audit_state = result
        except Exception:
            # Keep the existing bounded/drop-on-error contract. Never retry the
            # speech, business turn or persistence operation after uncertainty.
            audit_state = "uncertain"
        if sequence == record._playback_audit_sequence:
            record.playback_audit_state = audit_state


# -- one dispatcher per session --------------------------------------------- #
_dispatchers: dict[str, SessionSpeechDispatcher] = {}


def dispatcher_for_session(
    session_id: str, channel: typing.Any = None, **kwargs
) -> SessionSpeechDispatcher:
    """Return THE dispatcher for this session (created on first use)."""
    key = str(session_id)
    dispatcher = _dispatchers.get(key)
    if dispatcher is None:
        if channel is None:
            raise SpeechDispatchError("channel required for a new dispatcher")
        dispatcher = SessionSpeechDispatcher(key, channel, **kwargs)
        _dispatchers[key] = dispatcher
    return dispatcher


def release_dispatcher(session_id: str) -> None:
    """Drop the session's dispatcher (idempotent)."""
    _dispatchers.pop(str(session_id), None)
