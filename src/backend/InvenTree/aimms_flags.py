"""AIMMS flag registry — the single declaration point for both planes (S44).

Every AIMMS feature flag is declared HERE exactly once, with its canonical
environment name, kind, default, and which plane(s) consume it:

- ``django`` — bridged into Django settings by the loop in
  ``InvenTree/settings.py`` (module-level names identical to the historic
  hand-written bridge).
- ``ai`` — a field on the pydantic ``ai.core.config.Settings`` object
  (``ai_field`` names it). The env name is accepted verbatim, and the
  ``AIMMS_``-prefixed form also works (AliasChoices).
- ``both`` — one env var read independently by both planes; the parity test
  asserts the defaults agree so the planes cannot silently diverge.

This module is imported by ``InvenTree/settings.py`` at boot, so it must be
stdlib-only and import-light. It declares — it never reads the environment
itself; each plane keeps its own reader (``InvenTree.config`` helpers /
pydantic-settings) and the island parity test
(``ai/core/tests/test_flag_registry.py``) enforces that a flag added to one
place without the other fails CI.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FlagEntry:
    """One declared AIMMS flag."""

    env_name: str
    kind: str  # bool | str | csv | float | int | literal
    default: object
    planes: str  # django | ai | both
    config_key: str | None = None  # Django config-file key (django/both only)
    ai_field: str | None = None  # ai.core Settings field name (ai/both only)
    description: str = ''


REGISTRY: tuple[FlagEntry, ...] = (
    # --- Django plane (the historic settings.py bridge, order preserved) ---
    FlagEntry(
        'AIMMS_WORK_ORDERS_ENABLED',
        'bool',
        False,
        'django',
        config_key='aimms_work_orders_enabled',
        description='Work-order surfaces master gate',
    ),
    FlagEntry(
        'AIMMS_MACHINE_AI_READ_ENABLED',
        'bool',
        False,
        'django',
        config_key='aimms_machine_ai_read_enabled',
        description='Machine read tools for the AI planes',
    ),
    FlagEntry(
        'AIMMS_MAINTENANCE_AI_READ_ENABLED',
        'bool',
        False,
        'django',
        config_key='aimms_maintenance_ai_read_enabled',
        description='Maintenance read tools for the AI planes',
    ),
    FlagEntry(
        'AIMMS_MAINTENANCE_SCOPE_RESOLVER',
        'str',
        None,
        'django',
        config_key='aimms_maintenance_scope_resolver',
        description='Dotted path; empty = unresolved (fail closed)',
    ),
    FlagEntry(
        'AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER',
        'str',
        None,
        'django',
        config_key='aimms_diagnostic_capability_resolver',
        description='Dotted path consulted by the reasoning rail',
    ),
    FlagEntry(
        'AIMMS_SINGLE_SITE_CLIENT_CODE',
        'str',
        'internal',
        'django',
        config_key='aimms_single_site_client_code',
        description='Tenant code the single-site resolver grants',
    ),
    FlagEntry(
        'AIMMS_PLANT_TIMEZONE',
        'str',
        '',
        'django',
        config_key='aimms_plant_timezone',
        description='IANA timezone for analytics time buckets; empty = TIME_ZONE',
    ),
    FlagEntry(
        'AIMMS_CLOSEOUT_EXTRACTION_ENABLED',
        'bool',
        False,
        'django',
        config_key='aimms_closeout_extraction_enabled',
        description='Closeout extraction master gate (S19)',
    ),
    FlagEntry(
        'AIMMS_CLOSEOUT_EXTRACTOR',
        'str',
        None,
        'django',
        config_key='aimms_closeout_extractor',
        description='Dotted path to the extraction binding',
    ),
    FlagEntry(
        'AIMMS_CLOSEOUT_EXTRACTION_MODEL',
        'str',
        '',
        'django',
        config_key='aimms_closeout_extraction_model',
        description='Model override for closeout extraction',
    ),
    FlagEntry(
        'AIMMS_CLOSEOUT_WIZARD_ENABLED',
        'bool',
        False,
        'django',
        config_key='aimms_closeout_wizard_enabled',
        description='Closeout capture wizard',
    ),
    FlagEntry(
        'AIMMS_RISK_RADAR_ENABLED',
        'bool',
        False,
        'django',
        config_key='aimms_risk_radar_enabled',
        description='Risk radar scans + findings API (S21)',
    ),
    FlagEntry(
        'AIMMS_COMMAND_CENTER_ENABLED',
        'bool',
        False,
        'django',
        config_key='aimms_command_center_enabled',
        description='Command center surface',
    ),
    FlagEntry(
        'AIMMS_RISK_NOTIFICATIONS_ENABLED',
        'bool',
        False,
        'django',
        config_key='aimms_risk_notifications_enabled',
        description='Risk notification sweeps',
    ),
    FlagEntry(
        'AIMMS_RISK_SERVICE_USER_ID',
        'str',
        None,
        'django',
        config_key='aimms_risk_service_user_id',
        description='Least-privilege scanner principal (unset = fail closed)',
    ),
    FlagEntry(
        'AIMMS_RISK_RULES_ENABLED',
        'csv',
        '',
        'django',
        config_key='aimms_risk_rules_enabled',
        description='Comma-separated rule codes; empty enables nothing',
    ),
    FlagEntry(
        'AIMMS_ATTACHMENT_RAG_ENABLED',
        'bool',
        True,
        'django',
        config_key='aimms_attachment_rag_enabled',
        description=(
            'Attachment RAG ingest receivers/tasks (Django plane; default-on '
            'since R5 — the receiver additionally requires the effective '
            'AI-plane ingest flag, which self-degrades without providers)'
        ),
    ),
    FlagEntry(
        'AIMMS_MEDIA_RAG_ENABLED',
        'bool',
        True,
        'django',
        config_key='aimms_media_rag_enabled',
        description=(
            'Media RAG ingest arm — Django-plane co-gate ANDed with '
            'FEATURE_MEDIA_RAG_INGEST (R3; video R4; default-on since R5)'
        ),
    ),
    FlagEntry(
        'FEATURE_THREAD_SHARING',
        'bool',
        False,
        'both',
        config_key='feature_thread_sharing',
        ai_field='feature_thread_sharing',
        description='B6 thread sharing (both planes read one env var)',
    ),
    FlagEntry(
        'FEATURE_AI_THREAD_SCOPE_SHADOW',
        'bool',
        True,
        'both',
        config_key='feature_ai_thread_scope_shadow',
        ai_field='feature_ai_thread_scope_shadow',
        description=(
            'S1 analysis rail: store/resolve the thread analysis scope and '
            'log would-be out-of-scope results without changing answers'
        ),
    ),
    FlagEntry(
        'FEATURE_AI_THREAD_SCOPE_ENFORCE',
        'bool',
        False,
        'both',
        config_key='feature_ai_thread_scope_enforce',
        ai_field='feature_ai_thread_scope_enforce',
        description=(
            'S5 analysis rail: reject out-of-scope retrieval results under '
            'an explicit analysis scope (rollback floor once enabled)'
        ),
    ),
    FlagEntry(
        'FEATURE_AI_ANALYSIS_ROUTER_SHADOW',
        'bool',
        True,
        'ai',
        ai_field='feature_ai_analysis_router_shadow',
        description=(
            'S3 analysis rail: classify task/effect intent and log '
            'divergence from the legacy route without changing it'
        ),
    ),
    FlagEntry(
        'FEATURE_AI_ANALYSIS_ROUTER_ENFORCE',
        'bool',
        False,
        'ai',
        ai_field='feature_ai_analysis_router_enforce',
        description=(
            'S3 analysis rail: route read-only analysis intents to '
            'RouteMode.ANALYSIS (dark until the Phase-4 evidence gate)'
        ),
    ),
    FlagEntry(
        'AIMMS_ANALYSIS_INTENT_HOLDBACK',
        'str',
        '',
        'ai',
        ai_field='aimms_analysis_intent_holdback',
        description=(
            'S7 rollout: csv of intent values held on the legacy rail '
            'despite a shipped executor (per-intent stage/rollback)'
        ),
    ),
    # --- ai plane (pydantic Settings fields; canonical env name = alias) ---
    FlagEntry(
        'FEATURE_WF1_DIAGNOSTICS',
        'bool',
        True,
        'ai',
        ai_field='feature_wf1_diagnostics',
    ),
    FlagEntry(
        'FEATURE_WF2_SEQUENTIAL', 'bool', True, 'ai', ai_field='feature_wf2_sequential'
    ),
    FlagEntry(
        'FEATURE_WF3_CONCURRENT', 'bool', True, 'ai', ai_field='feature_wf3_concurrent'
    ),
    FlagEntry(
        'FEATURE_WF4_PROCUREMENT',
        'bool',
        True,
        'ai',
        ai_field='feature_wf4_procurement',
    ),
    FlagEntry('FEATURE_WF5_CPQ', 'bool', True, 'ai', ai_field='feature_wf5_cpq'),
    FlagEntry(
        'FEATURE_WF6_INCOMING_DOCS',
        'bool',
        True,
        'ai',
        ai_field='feature_wf6_incoming_docs',
    ),
    FlagEntry('FEATURE_WF8_LOOKUP', 'bool', True, 'ai', ai_field='feature_wf8_lookup'),
    FlagEntry(
        'FEATURE_CAPABILITY_BROKER_SHADOW',
        'bool',
        True,
        'ai',
        ai_field='feature_capability_broker_shadow',
    ),
    FlagEntry(
        'FEATURE_CAPABILITY_BROKER_ENFORCE',
        'bool',
        True,
        'ai',
        ai_field='feature_capability_broker_enforce',
    ),
    FlagEntry(
        'FEATURE_CAPABILITY_SELECTION_V2',
        'bool',
        True,
        'ai',
        ai_field='feature_capability_selection_v2',
    ),
    FlagEntry(
        'FEATURE_CATEGORY_LEXICON',
        'bool',
        True,
        'ai',
        ai_field='feature_category_lexicon',
    ),
    FlagEntry(
        'FEATURE_QUESTION_CARDS', 'bool', False, 'ai', ai_field='feature_question_cards'
    ),
    FlagEntry(
        'FEATURE_REFLECTION_MIDDLEWARE',
        'bool',
        True,
        'ai',
        ai_field='feature_reflection_middleware',
    ),
    FlagEntry(
        'FEATURE_DISTRIBUTED_RATE_LIMIT_SHADOW',
        'bool',
        True,
        'ai',
        ai_field='feature_distributed_rate_limit_shadow',
    ),
    FlagEntry(
        'FEATURE_DISTRIBUTED_RATE_LIMIT_ENFORCE',
        'bool',
        False,
        'ai',
        ai_field='feature_distributed_rate_limit_enforce',
    ),
    FlagEntry(
        'FEATURE_TOKEN_BUDGET_SHADOW',
        'bool',
        True,
        'ai',
        ai_field='feature_token_budget_shadow',
    ),
    FlagEntry(
        'FEATURE_TOKEN_BUDGET_ENFORCE',
        'bool',
        False,
        'ai',
        ai_field='feature_token_budget_enforce',
    ),
    FlagEntry(
        'FEATURE_AI_QUOTA_PROFILES',
        'bool',
        False,
        'ai',
        ai_field='feature_ai_quota_profiles',
        description=(
            'S12: finite quota profiles (standard/evaluation/service) with '
            'reservation/settlement accounting. Blocking stays governed by the '
            'FEATURE_TOKEN_BUDGET_* pair; enforcement also requires the '
            'verified shared cache backend.'
        ),
    ),
    FlagEntry(
        'FEATURE_AI_ADMISSION_CONTROL_SHADOW',
        'bool',
        True,
        'ai',
        ai_field='feature_ai_admission_control_shadow',
        description='S13: log admission.would_reject without rejecting.',
    ),
    FlagEntry(
        'FEATURE_AI_ADMISSION_CONTROL_ENFORCE',
        'bool',
        False,
        'ai',
        ai_field='feature_ai_admission_control_enforce',
        description=(
            'S13: reject saturated turns with typed 503 ai_capacity_busy. '
            'Fails open on store errors (availability control).'
        ),
    ),
    FlagEntry(
        'FEATURE_MODEL_TIERING_SHADOW',
        'bool',
        True,
        'ai',
        ai_field='feature_model_tiering_shadow',
    ),
    FlagEntry(
        'FEATURE_MODEL_TIERING_ENFORCE',
        'bool',
        False,
        'ai',
        ai_field='feature_model_tiering_enforce',
    ),
    FlagEntry(
        'FEATURE_WF8_TEXT_FAST_TIER',
        'bool',
        False,
        'ai',
        ai_field='feature_wf8_text_fast_tier',
    ),
    FlagEntry(
        'FEATURE_TYPED_TURN_FAILURES',
        'bool',
        False,
        'ai',
        ai_field='feature_typed_turn_failures',
    ),
    FlagEntry(
        'FEATURE_THREAD_COMPACTION_SHADOW',
        'bool',
        False,
        'ai',
        ai_field='feature_thread_compaction_shadow',
    ),
    FlagEntry(
        'FEATURE_THREAD_COMPACTION',
        'bool',
        False,
        'ai',
        ai_field='feature_thread_compaction',
    ),
    FlagEntry(
        'FEATURE_MEMORY_RAIL_REPLAY',
        'bool',
        False,
        'ai',
        ai_field='feature_memory_rail_replay',
    ),
    FlagEntry(
        'FEATURE_PROMPT_CACHE_STABLE_TOOLS',
        'bool',
        False,
        'ai',
        ai_field='feature_prompt_cache_stable_tools',
    ),
    FlagEntry(
        'FEATURE_NLI_GROUNDEDNESS',
        'bool',
        False,
        'ai',
        ai_field='feature_nli_groundedness',
    ),
    FlagEntry(
        'FEATURE_VOICE_LIVE_DIAGNOSIS',
        'bool',
        False,
        'ai',
        ai_field='feature_voice_live_diagnosis',
    ),
    FlagEntry(
        'FEATURE_VOICE_READONLY_TOOLS',
        'bool',
        True,
        'ai',
        ai_field='feature_voice_readonly_tools',
    ),
    FlagEntry(
        'FEATURE_VOICE_FAST_PATH',
        'bool',
        False,
        'ai',
        ai_field='feature_voice_fast_path',
    ),
    FlagEntry(
        'FEATURE_VOICE_WRITE_CONFIRMATION',
        'bool',
        True,
        'ai',
        ai_field='feature_voice_write_confirmation',
    ),
    FlagEntry(
        'FEATURE_TURN_USAGE_PERSISTENCE',
        'bool',
        True,
        'ai',
        ai_field='feature_turn_usage_persistence',
    ),
    FlagEntry(
        'FEATURE_ENTITY_MANIFEST',
        'bool',
        True,
        'ai',
        ai_field='feature_entity_manifest',
    ),
    FlagEntry(
        'FEATURE_MEDIA_EVIDENCE', 'bool', True, 'ai', ai_field='feature_media_evidence'
    ),
    FlagEntry(
        'FEATURE_HISTORY_ENRICHMENT',
        'bool',
        False,
        'ai',
        ai_field='feature_history_enrichment',
    ),
    FlagEntry('FEATURE_VOICE_LIVE', 'bool', False, 'ai', ai_field='feature_voice_live'),
    FlagEntry(
        'FEATURE_VOICE_LIVE_WEBRTC',
        'bool',
        False,
        'ai',
        ai_field='feature_voice_live_webrtc',
    ),
    FlagEntry(
        'FEATURE_VOICE_LIVE_RELAY',
        'bool',
        False,
        'ai',
        ai_field='feature_voice_live_relay',
    ),
    FlagEntry(
        'FEATURE_VOICE_NATIVE_STS',
        'bool',
        False,
        'ai',
        ai_field='feature_voice_native_sts',
    ),
    FlagEntry(
        'FEATURE_GUIDED_PROCEDURES',
        'bool',
        False,
        'ai',
        ai_field='feature_guided_procedures',
        description='B7 guided procedure walkthrough',
    ),
    FlagEntry(
        'FEATURE_TOKEN_STREAMING',
        'bool',
        False,
        'ai',
        ai_field='feature_token_streaming',
        description='S45 real token streaming on the wf8 text rail',
    ),
    FlagEntry(
        'FEATURE_TOOL_EVENTS',
        'bool',
        False,
        'ai',
        ai_field='feature_tool_events',
        description='S46 content-free tool/step events on the chat stream',
    ),
    FlagEntry(
        'FEATURE_AGUI_ENDPOINT',
        'bool',
        False,
        'ai',
        ai_field='feature_agui_endpoint',
        description='S49 spec-clean AG-UI adapter route (/agui)',
    ),
    FlagEntry(
        'FEATURE_ATTACHMENT_RAG_INGEST',
        'bool',
        True,
        'ai',
        ai_field='feature_attachment_rag_ingest',
        description=(
            'Attachment RAG doc-pipeline ingestion (default-on since R5; '
            'degrades off without providers unless explicitly set)'
        ),
    ),
    FlagEntry(
        'FEATURE_ATTACHMENT_RAG_RETRIEVAL',
        'bool',
        True,
        'ai',
        ai_field='feature_attachment_rag_retrieval',
        description=(
            'search_attachment_docs tool exposure (R2; default-on since R5, '
            'provider-degrading)'
        ),
    ),
    FlagEntry(
        'FEATURE_MEDIA_RAG_INGEST',
        'bool',
        True,
        'ai',
        ai_field='feature_media_rag_ingest',
        description=(
            'Image/video RAG ingestion router arm (R3/R4; default-on since '
            'R5, provider-degrading)'
        ),
    ),
    FlagEntry(
        'FEATURE_MEDIA_RAG_RETRIEVAL',
        'bool',
        True,
        'ai',
        ai_field='feature_media_rag_retrieval',
        description=(
            'search_evidence_media tool exposure (R3; default-on since R5, '
            'provider-degrading)'
        ),
    ),
    FlagEntry(
        'AIMMS_MANUAL_GROUNDING_MODE',
        'literal',
        'shadow',
        'ai',
        ai_field='manual_grounding_mode',
        description='off | shadow | enforce; enforce flip is human-gated',
    ),
    FlagEntry(
        'AIMMS_EVIDENCE_GATE_MODE',
        'literal',
        'shadow',
        'ai',
        ai_field='evidence_gate_mode',
        description='S10 evidence gate: off | shadow | enforce; enforce is human-gated',
    ),
    FlagEntry(
        'AIMMS_CHAT_HISTORY_MESSAGES', 'int', 12, 'ai', ai_field='chat_history_messages'
    ),
    FlagEntry(
        'AIMMS_VOICE_CONFIDENCE_FLOOR',
        'float',
        0.85,
        'ai',
        ai_field='voice_confidence_floor',
    ),
    FlagEntry(
        'FEATURE_AI_PILOT_STOP_LATCH',
        'bool',
        False,
        'both',
        config_key='feature_ai_pilot_stop_latch',
        ai_field='feature_ai_pilot_stop_latch',
        description=(
            'Production emergency stop (kill switch): arm the fail-closed '
            'admission gate. The '
            'latch models and pilot_stop/pilot_resume commands work with the '
            'flag DARK (operator drills); only the per-turn check is gated. '
            'Enable on the pilot deployment after verifying the shared cache.'
        ),
    ),
    FlagEntry(
        'AIMMS_PILOT_STOP_OWNERS',
        'csv',
        None,
        'django',
        config_key='aimms_pilot_stop_owners',
        description=(
            'Q43 Part-4 naming: role:username pairs for the five stop-authority '
            'owners, e.g. engineering:alice,product:bob,... Feeds latch alerts.'
        ),
    ),
    FlagEntry(
        'FEATURE_AI_RETENTION_JOBS',
        'bool',
        False,
        'both',
        config_key='feature_ai_retention_jobs',
        ai_field='feature_ai_retention_jobs',
        description=(
            'S16/Q48: run the scheduled retention purge/reconciliation jobs '
            '(400-day transcripts, 90-day detail, aggregates). Dark by '
            'default AND by owner decision (2026-08-29: data kept); the '
            'uploads TTL sweep and outbox drain run regardless. Day counts '
            'are code constants, not settings.'
        ),
    ),
)


def django_flags() -> tuple[FlagEntry, ...]:
    """Entries the Django settings loop bridges (planes django or both)."""
    return tuple(entry for entry in REGISTRY if entry.planes in ('django', 'both'))


def ai_flags() -> tuple[FlagEntry, ...]:
    """Entries backed by an ai.core Settings field (planes ai or both)."""
    return tuple(entry for entry in REGISTRY if entry.planes in ('ai', 'both'))


__all__ = ['REGISTRY', 'FlagEntry', 'ai_flags', 'django_flags']
