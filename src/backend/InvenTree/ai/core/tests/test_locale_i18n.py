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
