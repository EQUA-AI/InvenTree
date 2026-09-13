"""State and permission-derived spoken help, not an executable intent."""

from ai.core.voice.experience import write_locale_reason, writes_eligible


def compose_help(principal, session, settings) -> str:
    """Only advertise the current actor's qualified controls and review path."""
    text = (
        "Ask a question. Say stop speaking to interrupt, repeat that, next three, "
        "slower, short version, or turn sounds off. You can switch to push to talk on screen. "
    )
    if not writes_eligible(session.locale):
        return text + write_locale_reason(session.locale)
    if settings.feature_voice_decision_coordinator:
        from ai.core.decisions.coordinator import get_coordinator

        decision = get_coordinator().store.read(session.thread_id)
        if decision and decision.actor_user_pk == str(principal.user_pk):
            if decision.state == "presented":
                return (
                    text
                    + "A decision is waiting. Say what am I confirming to review it, or set that aside."
                )
            if decision.operation_id:
                text += "Say what happened to my last action to check its receipt. "
    if settings.feature_voice_approvals:
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.get(pk=principal.user_pk)
        if user.has_perm("approvals.review"):
            text += "You can ask for your assigned approval inbox. "
    return text
