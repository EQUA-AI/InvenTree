"""Pending-resolution stage: injection → safety → voice write → question (S47/S4).

The ORDERING here is the security control: an injected or safety-refused
turn may neither confirm a stored write nor reach a workflow, and it still
CLOSES both pending windows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai.core.turn.state import TurnRun
    from ai.core.turn_service import NormalizedTurnService


async def resolve_preconditions(service: NormalizedTurnService, run: TurnRun) -> None:
    """Resolve injection refusal and pending write/question, in that order."""

    from ai.core.decisions import pipeline as decisions

    # First of all: an attempt to rewrite the assistant's instructions is
    # refused outright. This must precede BOTH the pending-write
    # resolution and routing -- an injected turn may neither confirm a
    # stored write nor reach a workflow. Ordering is the control here.
    run.injection_canonical = await service._refuse_instruction_override(
        content=run.content,
        modality=run.modality,
        thread_id=run.thread.pk,
        turn_id=run.turn.pk,
        emitter=run.emitter,
    )
    # Before routing: a confirmation reply to a pending Tier-3 write
    # ("yes"/"confirm delete") would otherwise route like any request,
    # so the pending confirmation must capture this turn first.
    if run.injection_canonical is not None:
        await decisions.abandon(service, run, "injection_refused")
        # A refused turn must still CLOSE the confirmation window. Merely
        # skipping resolution left the proposal armed, so a bare "yes"
        # one turn later executed it -- the injection would have been a
        # way to step over the one-turn window rather than be stopped by
        # it.
        service._abandon_pending_voice_write(modality=run.modality, thread_id=run.thread.pk)
        # The same reasoning closes the question window (S22 invariant
        # 3): a refused turn abandons the pending question.
        service._abandon_pending_question(thread_id=run.thread.pk)
        run.write_canonical = None
        run.question_resolution = None
    else:
        # S4: a request to skip or defeat a safety control is refused
        # deterministically, for BOTH modalities, BEFORE a pending write
        # could be confirmed, BEFORE a focus switch could capture the turn,
        # and before routing could reach a workflow. A bypass-phrased
        # "confirmation" must refuse AND close the window — never execute.
        # (Ordering is the control: the focus rail below may never step over
        # this refusal.)
        run.safety_response = await service._refuse_unsafe_shortcut(
            content=run.content,
            modality=run.modality,
            thread_id=run.thread.pk,
            turn_id=run.turn.pk,
            emitter=run.emitter,
            locale=getattr(run.trusted_context, "locale", "en"),
        )
        if run.safety_response is not None:
            await decisions.abandon(service, run, "safety_refused")
            service._abandon_pending_voice_write(modality=run.modality, thread_id=run.thread.pk)
            service._abandon_pending_question(thread_id=run.thread.pk)
            run.write_canonical = None
            run.question_resolution = None
            return
        # Focus rail: an EXPLICIT switch ("talk about machine 3") captures
        # the turn AFTER the safety refusal above and BEFORE any pending
        # window can mistake it for an answer — an option ordinal buried in
        # a switch phrase must never select, and a successful switch sets
        # aside any unconfirmed pending decision through its coordinator
        # (fail closed; a later "yes" cannot apply an old-target preview to
        # the new focus). Unmatched/unauthorized switches and clarifications
        # leave focus unchanged.
        from ai.core.focus import commands as focus_commands

        run.focus_canonical = await focus_commands.maybe_handle(service, run)
        if run.focus_canonical is not None:
            # The focus turn consumed the one-turn question window.
            service._abandon_pending_question(thread_id=run.thread.pk)
            run.question_resolution = None
            return
        if await decisions.resolve(service, run):
            return
        run.write_canonical = await service._resolve_pending_voice_write(
            actor=run.actor,
            trusted_context=run.trusted_context,
            content=run.content,
            modality=run.modality,
            thread_id=run.thread.pk,
            turn_id=run.turn.pk,
            emitter=run.emitter,
            run=run,
        )
        if run.write_canonical is not None:
            # A write confirmation captured the turn; the question slot
            # is consumed and discarded -- it was not answered.
            service._abandon_pending_question(thread_id=run.thread.pk)
            run.question_resolution = None
        else:
            # S22 answer binder: consume-on-read, exactly once. A
            # non-answer returns None and the turn routes normally.
            run.question_resolution = service._resolve_pending_question(
                content=run.content, modality=run.modality, thread_id=run.thread.pk
            )
            if run.question_resolution is not None:
                # A CONFIRMED focus-selection option (server-owned ref) may
                # update the durable focus; unmatched/declined answers and
                # every other question source leave focus unchanged.
                from ai.core.focus import commands as focus_commands

                run.focus_canonical = await focus_commands.apply_question_answer(service, run)
