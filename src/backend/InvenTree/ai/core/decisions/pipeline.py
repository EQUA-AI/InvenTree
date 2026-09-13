"""Decision precedence in the shared normalized-turn pipeline."""

from contextvars import ContextVar

from ai.core.decisions.coordinator import DecisionConflict, DecisionReply, get_coordinator
from ai.core.decisions.store import DecisionStoreUnavailable

provider_activity = ContextVar("voice_decision_provider_activity", default=False)


async def process_with_playback_probe(service, channel, **kwargs):
    """Ephemeral provider activity must not alter a turn's durable replay fingerprint."""
    token = provider_activity.set(
        bool(channel and getattr(channel, "has_active_app_response", lambda: False)())
    )
    try:
        return await service.process(**kwargs)
    finally:
        provider_activity.reset(token)


def enabled():
    """The registered coordinator flag owns this entire rail."""
    from ai.core.config import get_settings

    return bool(getattr(get_settings(), "feature_voice_decision_coordinator", False))


async def abandon(service, run, reason):
    """Safety refusals close the decision window before any other pending rail."""
    if enabled():
        await service._call_sync(get_coordinator().disarm, run.thread.pk, reason)


async def resolve(service, run):
    """Capture focused decisions before legacy writes/questions, then work-order intents."""
    if not enabled() or run.modality != "voice":
        return False
    from ai.core.decisions.inventory import InventoryAmbiguity, pending_selection
    from ai.core.voice.experience import writes_eligible

    if not writes_eligible(getattr(run.trusted_context, "locale", "en")):
        await abandon(service, run, "locale_not_qualified")
        return False
    try:
        coordinator = get_coordinator()
        current = await service._call_sync(coordinator.store.read, run.thread.pk)
        # Legacy question storage has no peek: taking the contradictory question
        # also ensures it cannot be answered on the next turn after refusal.
        if current and current.state == "presented" and service.question_store.take(run.thread.pk):
            reply = await service._call_sync(
                coordinator.disarm, run.thread.pk, "question_contradiction"
            )
            reply = DecisionReply(
                "Two confirmation contexts were present. I set them aside; request a fresh preview.",
                reply.decision,
                "question_contradiction",
            )
        else:
            arguments = {
                "actor": run.actor,
                "session_id": run.metadata.get("voice_session_id", ""),
                "thread_id": str(run.thread.pk),
                "nonce": str(run.turn.pk),
            }
            reply = await service._call_sync(
                coordinator.resolve,
                run.content,
                **arguments,
                context=run.metadata.get("decision_context"),
                provider_active=provider_activity.get(),
            )
            if reply is None or reply.route_normally:
                if reply:
                    run.pre_speech_status = reply.spoken
                reply = await pending_selection(service, run, coordinator, arguments)
                if reply is None:
                    reply = await service._call_sync(coordinator.begin, run.content, **arguments)
                if reply is None:
                    return False
    except InventoryAmbiguity as exc:
        from dataclasses import asdict

        from ai.core.config import get_settings
        from ai.core.decisions.inventory import parse_stock_intent
        from ai.core.questions.promotion import set_question_proposal
        from ai.core.questions.schema import render_question_text

        speech = render_question_text(str(exc), exc.options, modality="voice")
        if not get_settings().feature_question_cards or len(speech) > 700:
            reply = DecisionReply(
                "The inventory identity is ambiguous. Use an exact IPN or full location path.",
                event="refused",
            )
        else:
            owner, (_, scope_hash) = await service._call_sync(
                coordinator.adapter.owner_scope, run.actor
            )
            for option in exc.options:
                option["ref"].update(
                    actor_id=owner.pk,
                    scope_hash=scope_hash,
                    session_id=arguments["session_id"],
                    intent=asdict(
                        getattr(exc, "resume_intent", None) or parse_stock_intent(run.content)
                    ),
                )
            set_question_proposal({
                "source": "voice_inventory",
                "question_text": str(exc),
                "options": exc.options,
            })
            reply = DecisionReply(speech, event="question")
    except (DecisionConflict, DecisionStoreUnavailable) as exc:
        reply = DecisionReply(str(exc), event="refused")
    except Exception:
        # Domain refusals include unauthorized/ambiguous targets. Do not route
        # them into a different executor, and do not expose private row details.
        reply = DecisionReply(
            "I could not validate that decision. Ask for a fresh preview or your assigned approval inbox, or review it on screen.",
            event="refused",
        )
    run.write_canonical = await service._canonical_for_voice_write(
        thread_id=run.thread.pk,
        turn_id=run.turn.pk,
        spoken=reply.spoken,
        emitter=run.emitter,
        workflow_id="voice_decision",
        workflow_name="VOICE_DECISION",
    )
    run.write_canonical.update(
        pending_decision=reply.decision.to_public_dict() if reply.decision else None,
        decision_event=reply.event_dict(),
    )
    run.question_resolution = None
    return True
