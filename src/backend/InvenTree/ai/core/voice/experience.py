"""Versioned foreground voice policy; client preferences confer no authority."""

from __future__ import annotations

CONSENT_VERSION = "consent-v2"
CONSENT_IDLE_TIMEOUT_S = 300
VOCABULARY_VERSION = "aimms-site-v1"
VOCABULARY_OWNER = "AIMMS"

WRITE_LOCALE_REASONS = {
    "en": "Voice changes are available only in English. Use the on-screen review in this language.",
    "es": "Los cambios por voz solo están disponibles en inglés. Revisa la acción en pantalla.",
    "de": "Änderungen per Sprache sind nur auf Englisch verfügbar. Bitte am Bildschirm prüfen.",
    "fr": "Les modifications vocales sont disponibles uniquement en anglais. Vérifiez à l'écran.",
}


def voice_pairs() -> tuple[tuple[str, str], ...]:
    """Reuse the provider's single allow-list, never arbitrary provider names."""
    from ai.core.voice.gateway import VoiceLiveChannel

    return tuple(VoiceLiveChannel.USER_VOICE_MAP.values())


def validate_preferences(locale: str, voice: str, consent_version: str) -> None:
    """Reject stale/missing consent and mismatched or unmapped output pairs."""
    if consent_version != CONSENT_VERSION:
        raise ValueError("VOICE_CONSENT_REQUIRED")
    if (voice, locale) not in voice_pairs():
        raise ValueError("VOICE_LOCALE_UNSUPPORTED")


def writes_eligible(locale: str) -> bool:
    """English-first grammar qualification; never infer eligibility from ASR."""
    return locale.lower().split("-")[0] == "en"


def write_locale_reason(locale: str) -> str:
    """Honest, localized pilot restriction for every mapped output locale."""
    return WRITE_LOCALE_REASONS.get(locale.lower().split("-")[0], WRITE_LOCALE_REASONS["en"])


def capability(settings) -> dict:
    """Non-secret contract; permission-sensitive help is composed per request."""
    enabled = settings.feature_voice_live
    foreground = enabled and settings.feature_voice_foreground_session
    pairs = voice_pairs()
    return {
        "enabled": enabled,
        "webrtc": enabled and settings.feature_voice_live_webrtc,
        "relay": enabled and settings.feature_voice_live_relay,
        "foreground_session": foreground,
        "decisions": enabled and settings.feature_voice_decision_coordinator,
        "inventory_actions": enabled
        and getattr(settings, "feature_voice_inventory_actions", False),
        "procedure_complete": enabled
        and getattr(settings, "feature_voice_procedure_complete", False),
        "guided_procedures": enabled and getattr(settings, "feature_guided_procedures", False),
        "closeout": enabled and getattr(settings, "feature_voice_closeout", False),
        "turn": False,
        "turn_provider": None,
        "direct_connect_timeout_s": getattr(settings, "voice_direct_connect_timeout_s", 12),
        "network_qualification": "direct_stun_only_unqualified_networks_require_testing",
        "prompts": enabled,
        "help": foreground,
        "presentation": foreground,
        "ice_servers": [{"urls": "stun:stun.l.google.com:19302"}],
        "mobile_surface": foreground,
        "wake_lock": foreground,
        "modes": ["continuous", "push_to_talk"],
        "default_mode": "continuous",
        "max_queued_turns": settings.voice_max_queued_turns,
        "idle_timeout_s": settings.voice_live_idle_timeout_s,
        "mic_silence_rms": settings.voice_mic_silence_rms,
        "mic_silence_window_s": settings.voice_mic_silence_window_s,
        "route_loss_pause": settings.voice_route_loss_pause,
        "tts_timeout_s": settings.voice_tts_timeout_s,
        "decision_max_armed_s": settings.voice_decision_max_armed_s,
        "reconnect": {"grace_s": 3, "max_attempts": 2},
        "locales": [locale for _, locale in pairs],
        "voices": [{"voice": voice, "locale": locale} for voice, locale in pairs],
        "default_locale": "en-US",
        "default_voice": "en-US-AvaNeural",
        "consent_version": CONSENT_VERSION,
        "confidence_floor": settings.voice_confidence_floor,
        "confidence_semantics": "provider_score_when_present_not_a_probability_guarantee",
        "unknown_confidence_policy": "critical_terms_review_otherwise_normal_decision_safety",
        "voice_write_locales": ["en-US"],
        "voice_write_ineligible_reasons": WRITE_LOCALE_REASONS,
        "vocabulary_version": VOCABULARY_VERSION,
        "vocabulary_owner": VOCABULARY_OWNER,
    }
