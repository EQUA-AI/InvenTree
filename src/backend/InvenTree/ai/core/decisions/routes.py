"""Authenticated decision controls and exact, bound playback reporting."""

from ai.core.decisions.coordinator import DecisionConflict, DecisionReply, get_coordinator
from ai.core.decisions.store import DecisionStoreUnavailable
from ai.core.voice.wire import VoiceDecisionContext
from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from fastapi import HTTPException


class DecisionActionRequest(VoiceDecisionContext):
    """Only focus references and user input; no actor, scope or executable fields."""

    confirm_phrase: str = ""
    utterance_id: str | None = None
    spoken_summary_hash: str | None = None


async def speak_reply(session, reply, coordinator):
    """Persist-before-speak for button/reconnect read-back requests."""
    from ai.core.voice import routes
    from ai.core.voice.speech import build_exact_tts_payload
    from voice.models import VoiceUtteranceType
    from voice.services import realtime

    utterance = await sync_to_async(realtime.persist_utterance, thread_sensitive=True)(
        session=session, utterance_type=VoiceUtteranceType.PROMPT, spoken_summary=reply.spoken
    )
    decision = reply.decision
    channel = (
        routes._provider_channel_factory(session) if routes._provider_channel_factory else None
    )
    send = getattr(channel, "send_control", None)
    if send is not None:
        if decision and decision.state == "presented" and reply.event in ("presented", "review"):
            decision = await sync_to_async(coordinator.bind_playback, thread_sensitive=True)(
                decision,
                utterance_id=str(utterance.pk),
                spoken_text=utterance.spoken_summary,
                spoken_hash=utterance.spoken_summary_hash,
            )
        try:
            speech = build_exact_tts_payload(
                persisted_text=utterance.spoken_summary,
                persisted_hash=utterance.spoken_summary_hash,
            )
            if decision and decision.utterance_id == str(utterance.pk):
                speech["response"]["metadata"] = {
                    "aimms_utterance_id": decision.utterance_id,
                    "aimms_spoken_hash": decision.spoken_summary_hash,
                }
            await send(speech)
            await sync_to_async(realtime.mark_playback, thread_sensitive=True)(
                utterance=utterance, state="requested"
            )
        except Exception:
            # Delivery is not outcome. Keep the preview visible and say pending.
            if decision and decision.state == "presented":
                decision = await sync_to_async(coordinator.advance, thread_sensitive=True)(
                    decision, delivery_state="failed"
                )
    return decision


def install_routes(router):
    """Register beneath the existing voice router and authentication boundary."""

    async def owned(session_id):
        from ai.core.voice import routes

        settings = routes._require_voice_enabled()
        actor = routes._principal()
        session = await routes._owned_session(actor, session_id, settings)
        try:
            coordinator = get_coordinator()
        except DecisionStoreUnavailable as exc:
            raise HTTPException(status_code=409, detail="VOICE_DECISION_UNAVAILABLE") from exc
        return actor, session, coordinator

    def payload(reply):
        return {
            "pending_decision": reply.decision.to_public_dict() if reply.decision else None,
            "decision_event": reply.event_dict(),
        }

    @router.get("/sessions/{session_id}/decision")
    async def read_decision(session_id: str):
        actor, session, coordinator = await owned(session_id)
        try:
            decision = await sync_to_async(coordinator.store.read)(session.thread_id)
            if decision is None:
                from ai.core.decisions.receipts import recover_latest

                recovered = await sync_to_async(recover_latest)(
                    actor=actor,
                    thread_id=session.thread_id,
                    session_id=session.pk,
                    now=coordinator.now(),
                )
                if recovered and await sync_to_async(coordinator.store.install)(recovered, None):
                    decision = recovered
                elif recovered:
                    decision = await sync_to_async(coordinator.store.read)(session.thread_id)
            if decision and decision.actor_user_pk != str(actor.user_pk):
                raise HTTPException(status_code=404, detail="VOICE_SESSION_FORBIDDEN")
            if decision and decision.state == "presented":
                if decision.session_id != str(session.pk):
                    reply = await sync_to_async(coordinator.disarm)(
                        session.thread_id, "reconnected", set_aside=True
                    )
                    return payload(reply)
                if coordinator.now() >= decision.expires_at:
                    decision = await sync_to_async(coordinator.advance)(decision, state="expired")
                else:
                    try:
                        await sync_to_async(coordinator.revalidate)(
                            decision, actor, str(session.pk)
                        )
                    except Exception:
                        return payload(
                            await sync_to_async(coordinator.disarm)(
                                session.thread_id, "source_invalidated"
                            )
                        )
            if decision and decision.state in ("executing", "resolved"):
                return payload(await sync_to_async(coordinator.history)(decision, actor))
            return payload(DecisionReply("", decision))
        except (DecisionStoreUnavailable, DecisionConflict) as exc:
            raise HTTPException(status_code=409, detail="VOICE_DECISION_CONFLICT") from exc

    @router.get("/sessions/{session_id}/operations/{operation_id}")
    async def read_operation(session_id: str, operation_id: str):
        from ai.core.decisions.receipts import lookup_operation

        actor, session, _ = await owned(session_id)
        try:
            result = await sync_to_async(lookup_operation)(
                actor=actor, thread_id=session.thread_id, operation_id=operation_id
            )
        except (ValueError, TypeError, ValidationError):
            result = None
        if result is None:
            raise HTTPException(status_code=404, detail="VOICE_OPERATION_NOT_FOUND")
        return result

    @router.post("/sessions/{session_id}/decision/{action}")
    async def act_on_decision(session_id: str, action: str, request: DecisionActionRequest):
        actor, session, coordinator = await owned(session_id)
        allowed = {
            "confirm",
            "cancel",
            "cancel-action",
            "disarm",
            "set-aside",
            "acknowledge-review",
            "repeat",
            "playback-started",
            "playback-completed",
        }
        if action not in allowed:
            raise HTTPException(status_code=404, detail="VOICE_DECISION_CONFLICT")
        try:
            decision = await sync_to_async(coordinator.store.read)(session.thread_id)
            if (
                decision is None
                or decision.actor_user_pk != str(actor.user_pk)
                or (
                    decision.session_id != str(session.pk)
                    and not (action == "repeat" and decision.state in ("executing", "resolved"))
                )
            ):
                raise DecisionConflict("No decision is armed in this session.")
            coordinator.check_context(decision, request.model_dump())
            if action == "repeat" and decision.state in ("executing", "resolved"):
                reply = await sync_to_async(coordinator.history)(decision, actor)
                reply = DecisionReply(
                    "I am reconciling your last action. " + reply.spoken, reply.decision, "receipt"
                )
                await speak_reply(session, reply, coordinator)
                return payload(reply)
            if action.startswith("playback-"):
                # Verify the persisted utterance itself, not merely a client hash.
                valid = await sync_to_async(
                    lambda: session.utterances.filter(
                        pk=request.utterance_id, spoken_summary_hash=request.spoken_summary_hash
                    ).exists()
                )()
                if not valid:
                    raise DecisionConflict("Unknown utterance.")
                decision = await sync_to_async(coordinator.playback)(
                    decision,
                    event=action,
                    sequence=request.sequence,
                    utterance_id=request.utterance_id,
                    spoken_hash=request.spoken_summary_hash,
                )
                from voice.services import realtime

                await sync_to_async(
                    lambda: session.utterances.filter(pk=request.utterance_id).update(
                        playback_state=str(decision.delivery_state)
                    )
                )()
                from ai.core.config import get_settings
                from ai.core.voice.routes import _limits

                await sync_to_async(realtime.touch_session)(
                    session=session, limits=_limits(get_settings()), count_turn=False
                )
                reply = DecisionReply("", decision, action)
            elif action in ("disarm", "set-aside", "cancel"):
                reply = await sync_to_async(coordinator.disarm)(
                    session.thread_id, action, set_aside=action == "set-aside"
                )
            elif action == "acknowledge-review":
                await sync_to_async(coordinator.revalidate)(decision, actor, session.pk)
                reply = DecisionReply(
                    "Review acknowledged. Confirmation is still required.",
                    await sync_to_async(coordinator.advance)(decision, review_acknowledged=True),
                )
            else:
                content = (
                    request.confirm_phrase
                    if action == "confirm"
                    else "cancel the action"
                    if action == "cancel-action"
                    else "repeat"
                )
                if action == "confirm" and not content:
                    raise DecisionConflict("An explicit confirmation is required.")
                reply = await sync_to_async(coordinator.resolve)(
                    content,
                    actor=actor,
                    session_id=session.pk,
                    thread_id=session.thread_id,
                    nonce=f"control:{decision.decision_id}:{decision.sequence}",
                    context=request.model_dump(),
                    touch=True,
                )
                if reply is None:
                    raise DecisionConflict("Request a fresh preview.")
                if action == "repeat" or reply.event == "receipt":
                    from dataclasses import replace

                    reply = replace(reply, decision=await speak_reply(session, reply, coordinator))
            return payload(reply)
        except (DecisionConflict, DecisionStoreUnavailable, ValueError, ValidationError) as exc:
            raise HTTPException(status_code=409, detail="VOICE_DECISION_CONFLICT") from exc
