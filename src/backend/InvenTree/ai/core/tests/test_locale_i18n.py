"""S33: locale threading and the per-locale deterministic template tables."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core import i18n_templates as i18n  # noqa: E402
from ai.core.voice import status_phrases  # noqa: E402
from ai.core.voice.provider import SessionPolicy  # noqa: E402


def test_trusted_context_locale_defaults_to_english():
    from ai.core.trusted_context import TrustedTurnContext

    context = TrustedTurnContext(
        actor="user:1",
        server_policy_key="site",
        server_policy_hash="hash",
        thread_namespace="unscoped",
        server_route_hints=(),
        allowed_capabilities=("read",),
        correlation_id="c",
        policy_version="v",
        untrusted_content="{}",
    )
    assert context.locale == "en"


def test_resolve_actor_locale_fails_safe_to_english():
    from ai.core.trusted_context import resolve_actor_locale

    # No such user / no database row — never raises, never guesses.
    assert resolve_actor_locale(99999999) == "en"
    assert resolve_actor_locale(None) == "en"


def test_localized_status_phrases_join_the_allowlist():
    for table in status_phrases.LOCALIZED_STATUS_PHRASES.values():
        for localized in table.values():
            assert localized in status_phrases.ALLOWED_STATUS_PHRASES
    # And the English originals remain members.
    assert status_phrases.THINKING in status_phrases.ALLOWED_STATUS_PHRASES


def test_localized_status_phrase_lookup_and_fallback():
    spanish = status_phrases.localized_status_phrase(status_phrases.THINKING, "es")
    assert spanish == "Déjame comprobarlo."
    assert status_phrases.localized_status_phrase(status_phrases.THINKING, "es-MX") == spanish
    # Unknown locale and unknown phrase both fall back to the input.
    assert (
        status_phrases.localized_status_phrase(status_phrases.THINKING, "xx")
        == status_phrases.THINKING
    )
    assert status_phrases.localized_status_phrase("not a phrase", "es") == "not a phrase"


def test_deterministic_template_locale_and_fallback():
    english = i18n.deterministic_template(i18n.ADVISORY_TEXT, "en")
    spanish = i18n.deterministic_template(i18n.ADVISORY_TEXT, "es")
    assert english != spanish
    assert i18n.deterministic_template(i18n.ADVISORY_TEXT, "xx") == english
    assert i18n.deterministic_template(i18n.ADVISORY_TEXT, None) == english
    # Region-qualified locales resolve to their base language.
    assert i18n.deterministic_template(i18n.ADVISORY_TEXT, "es-MX") == spanish


def test_advisory_canonical_speaks_the_locale():
    from ai.core.turn_service import _canonical_advisory_intent

    spanish = _canonical_advisory_intent(voice=True, locale="es")
    assert "registros por voz" in spanish.detailed_response
    assert spanish.spoken_summary == spanish.detailed_response
    assert spanish.safety_boundary == i18n.deterministic_template(i18n.SAFETY_BOUNDARY, "es")
    english = _canonical_advisory_intent(voice=True, locale="en")
    assert "by voice" in english.detailed_response


def test_grounding_downgrade_localizes():
    template = i18n.deterministic_template(i18n.GROUNDING_DOWNGRADE, "es")
    assert "verifica el" in template
    assert "{titles}" in template


def test_session_policy_voice_override_shape():
    from ai.core.voice.gateway import VoiceLiveChannel

    assert VoiceLiveChannel.USER_VOICE_MAP["en"] == ("en-US-AvaNeural", "en-US")
    voice_name, language = VoiceLiveChannel.USER_VOICE_MAP["es"]
    policy = SessionPolicy(voice_name=voice_name, language=language)
    payload = policy.session_update_payload()["session"]
    assert language in str(payload)


def test_wf8_locale_hint_appends_only_for_non_english():
    """W0: the run-input note appears for es and never for en/unknown."""
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow

    base = "how many pumps are in stock"
    unchanged = T1LookupWorkflow._with_locale_hint(base, {"locale": "en"})
    assert unchanged == base
    unknown = T1LookupWorkflow._with_locale_hint(base, {"locale": "xx"})
    # Unknown locale falls back to the English template → no note.
    assert unknown == base

    spanish = T1LookupWorkflow._with_locale_hint(base, {"locale": "es"})
    assert isinstance(spanish, list)
    texts = [c.text for m in spanish for c in m.contents]
    assert any("Responde en español" in t for t in texts)
    assert texts[0] == base


def test_luna_locale_directive_shapes():
    """W0: the Luna instructions gain the directive only for known non-en."""
    from ai.core.reasoning.luna_diagnostics import _locale_directive

    assert _locale_directive("en") == ""
    assert _locale_directive("en-US") == ""
    assert _locale_directive("xx") == ""
    assert "Responde en español" in _locale_directive("es")
    assert "Antworte auf Deutsch" in _locale_directive("de-DE")


def test_reasoning_envelope_carries_locale():
    from ai.core.reasoning.luna_diagnostics import TrustedReasoningEnvelope

    envelope = TrustedReasoningEnvelope(
        actor_id="user:1",
        scope={"policy_key": "site"},
        thread_id="thread_x",
        user_message="why is it vibrating",
        mode="text",
        policy_version="v1",
        correlation_id="c" * 32,
        locale="es",
    )
    assert envelope.locale == "es"
    assert (
        TrustedReasoningEnvelope(
            actor_id="user:1",
            scope={"policy_key": "site"},
            thread_id="thread_x",
            user_message="hello",
            mode="text",
            policy_version="v1",
            correlation_id="c" * 32,
        ).locale
        == "en"
    )
