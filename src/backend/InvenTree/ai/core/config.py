"""
AIMMS Configuration Module

Centralized typed configuration using pydantic-settings.
Loads from environment variables with validation.
"""

import logging
import re as _re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_AI_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    """
    AIMMS Backend Configuration.

    All settings are loaded from environment variables.
    Use .env file for local development.
    """

    model_config = SettingsConfigDict(
        env_prefix="AIMMS_",
        env_file=_AI_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        validate_by_name=True,
        case_sensitive=False,
    )

    # -------------------------------------------------------------------------
    # Application Settings
    # -------------------------------------------------------------------------
    env: Literal["development", "staging", "production"] = "development"
    debug: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "DEBUG"

    # -------------------------------------------------------------------------
    # Server Configuration
    # -------------------------------------------------------------------------
    # Loopback by default: the agent stack is mounted inside InvenTree.asgi, so
    # a standalone run is a dev convenience and must not bind publicly unless
    # a deployment explicitly sets AIMMS_HOST.
    host: str = "127.0.0.1"
    port: int = 8080

    # -------------------------------------------------------------------------
    # Authenticated AI Boundary
    # -------------------------------------------------------------------------
    # These fields deliberately have no aliases. The Settings env_prefix makes
    # their environment names AIMMS_SINGLE_SITE_POLICY_KEY, AIMMS_POLICY_VERSION,
    # AIMMS_SIGNED_SUBJECT_*, and AIMMS_ALLOWED_ORIGINS.
    single_site_policy_key: str = ""
    policy_version: str = "1"
    signed_subject_max_age_seconds: int = Field(default=120, gt=0, le=300)
    signed_subject_salt: str = "aimms.ai.interactive-subject.v1"
    signed_subject_audience: str = "aimms-ai"
    allowed_origins: list[str] = Field(default_factory=list)

    @field_validator(
        "single_site_policy_key",
        "policy_version",
        "signed_subject_salt",
        "signed_subject_audience",
        mode="after",
    )
    @classmethod
    def validate_ai_boundary_value(cls, value: str) -> str:
        """Reject whitespace-only AI boundary settings."""
        return value.strip()

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, value: str | list[str]) -> list[str]:
        """Parse exact AI boundary origins from a comma-separated value."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    # S35: the CWD-relative ./data storage tree (threads/checkpoints/cache
    # dirs plus their import-time mkdir) was deleted along with the file-JSON
    # memory providers that read it. Durable state lives in the database and
    # the shared Django cache only.

    # -------------------------------------------------------------------------
    # HITL Configuration
    # -------------------------------------------------------------------------
    hitl_timeout_seconds: int = Field(default=300, alias="HITL_TIMEOUT_SECONDS")
    hitl_auto_reject_on_timeout: bool = Field(default=False, alias="HITL_AUTO_REJECT_ON_TIMEOUT")

    # -------------------------------------------------------------------------
    # Feature Flags
    # -------------------------------------------------------------------------
    feature_wf1_diagnostics: bool = Field(
        default=True,
        validation_alias=AliasChoices("FEATURE_WF1_DIAGNOSTICS", "AIMMS_FEATURE_WF1_DIAGNOSTICS"),
    )
    feature_wf2_sequential: bool = Field(
        default=True,
        validation_alias=AliasChoices("FEATURE_WF2_SEQUENTIAL", "AIMMS_FEATURE_WF2_SEQUENTIAL"),
    )
    feature_wf3_concurrent: bool = Field(
        default=True,
        validation_alias=AliasChoices("FEATURE_WF3_CONCURRENT", "AIMMS_FEATURE_WF3_CONCURRENT"),
    )
    feature_wf4_procurement: bool = Field(
        default=True,
        validation_alias=AliasChoices("FEATURE_WF4_PROCUREMENT", "AIMMS_FEATURE_WF4_PROCUREMENT"),
    )
    feature_wf5_cpq: bool = Field(
        default=True, validation_alias=AliasChoices("FEATURE_WF5_CPQ", "AIMMS_FEATURE_WF5_CPQ")
    )
    feature_wf6_incoming_docs: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "FEATURE_WF6_INCOMING_DOCS", "AIMMS_FEATURE_WF6_INCOMING_DOCS"
        ),
    )
    feature_wf8_lookup: bool = Field(
        default=True,
        validation_alias=AliasChoices("FEATURE_WF8_LOOKUP", "AIMMS_FEATURE_WF8_LOOKUP"),
    )
    feature_capability_broker_shadow: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "FEATURE_CAPABILITY_BROKER_SHADOW", "AIMMS_FEATURE_CAPABILITY_BROKER_SHADOW"
        ),
    )
    feature_capability_broker_enforce: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "FEATURE_CAPABILITY_BROKER_ENFORCE", "AIMMS_FEATURE_CAPABILITY_BROKER_ENFORCE"
        ),
    )
    # Which workflows the invocation guard ENFORCES on, comma separated. Any
    # other workflow runs in shadow: the guard still evaluates and logs the
    # denial reason, but the call proceeds. The specialist rails soaked
    # shadow-first (S11) and were flipped live 2026-08-06 with zero denials
    # observed; enforcement everywhere is now the DEFAULT. The env var
    # remains an override, and the floor union in the guard keeps wf8/general/wf9
    # enforced no matter what an operator sets.
    capability_broker_enforced_workflows: str = Field(
        default="wf8,general,wf9,wf2,wf3,wf4,wf6",
        alias="CAPABILITY_BROKER_ENFORCED_WORKFLOWS",
    )
    # Capability selection v2: score the aggregation/threshold *shape* of a question
    # instead of a superlative keyword whitelist, keep the read-only SQL pack
    # attached to every read selection, and allow a second adjacent pack. Without
    # it, "how many X are over N" reaches no tool that can express the question.
    # Kill switch reverts to keyword-only selection.
    feature_capability_selection_v2: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "FEATURE_CAPABILITY_SELECTION_V2", "AIMMS_FEATURE_CAPABILITY_SELECTION_V2"
        ),
    )
    # Derive selection terms from live PartCategory names so deployment-specific
    # taxonomy ("Fasteners", "O-Rings") routes to the parts pack without a
    # hand-maintained synonym list. Separate flag: this is the only selection
    # input that touches the database and cache.
    feature_category_lexicon: bool = Field(
        default=True,
        validation_alias=AliasChoices("FEATURE_CATEGORY_LEXICON", "AIMMS_FEATURE_CATEGORY_LEXICON"),
    )
    # Structured question cards (S22/S23): a turn may COMPLETE with a
    # clarification question whose 2-4 options are server-derived; the answer
    # arrives as the next turn and is validated against the persisted record.
    # The flag gates ASKING only — the answer binder always runs, so flipping
    # this off drains any in-flight pending question harmlessly. Ships dark;
    # flips on only after the client that renders the card is live.
    feature_question_cards: bool = Field(
        default=False,
        validation_alias=AliasChoices("FEATURE_QUESTION_CARDS", "AIMMS_FEATURE_QUESTION_CARDS"),
    )
    feature_reflection_middleware: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "FEATURE_REFLECTION_MIDDLEWARE", "AIMMS_FEATURE_REFLECTION_MIDDLEWARE"
        ),
    )
    # S35: cross-replica rate limiting over the shared cache. Shadow runs the
    # fixed-window limiter next to the legacy in-process buckets and logs any
    # divergence; enforce hands the 429 decision to the windowed limiter.
    # Both off reverts to the per-process buckets alone.
    # S1 (analysis rail): server-owned thread analysis scope. Shadow stores
    # scopes, snapshots them per turn, and logs would-be out-of-scope
    # results without changing answers; enforce (S5) makes readers reject
    # out-of-scope results under an explicit scope. Enforce is part of the
    # rollback floor: once enabled in a deployment it is not turned back off.
    feature_ai_thread_scope_shadow: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "FEATURE_AI_THREAD_SCOPE_SHADOW", "AIMMS_FEATURE_AI_THREAD_SCOPE_SHADOW"
        ),
    )
    feature_ai_thread_scope_enforce: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_AI_THREAD_SCOPE_ENFORCE", "AIMMS_FEATURE_AI_THREAD_SCOPE_ENFORCE"
        ),
    )
    # S3 (analysis rail): typed task/effect intent ahead of complexity
    # routing. Shadow classifies, records span attrs, and logs divergence
    # from the legacy route without changing it; enforce routes analysis
    # intents (read-only, TEXT) to RouteMode.ANALYSIS — dark until the
    # evidence gate (Phase 4) gives that rail something real to execute.
    feature_ai_analysis_router_shadow: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "FEATURE_AI_ANALYSIS_ROUTER_SHADOW", "AIMMS_FEATURE_AI_ANALYSIS_ROUTER_SHADOW"
        ),
    )
    feature_ai_analysis_router_enforce: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_AI_ANALYSIS_ROUTER_ENFORCE", "AIMMS_FEATURE_AI_ANALYSIS_ROUTER_ENFORCE"
        ),
    )
    # S7 rollout: csv of intent VALUES held on the legacy rail despite a
    # shipped executor — the per-intent enforce stage/rollback knob (see
    # analysis.intent.held_back_intents). Empty = nothing held back.
    aimms_analysis_intent_holdback: str = Field(
        default="",
        validation_alias=AliasChoices("AIMMS_ANALYSIS_INTENT_HOLDBACK"),
    )
    feature_distributed_rate_limit_shadow: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "FEATURE_DISTRIBUTED_RATE_LIMIT_SHADOW", "AIMMS_FEATURE_DISTRIBUTED_RATE_LIMIT_SHADOW"
        ),
    )
    feature_distributed_rate_limit_enforce: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_DISTRIBUTED_RATE_LIMIT_ENFORCE", "AIMMS_FEATURE_DISTRIBUTED_RATE_LIMIT_ENFORCE"
        ),
    )
    # S37: per-user daily token budgets (UTC day, shared-cache counters).
    # 0 = unlimited. Shadow logs budget.would_block; enforce returns the
    # typed 429. An abuse control, not billing — see middleware/budget.py.
    ai_user_daily_token_budget: int = Field(
        default=1_000_000, ge=0, alias="AI_USER_DAILY_TOKEN_BUDGET"
    )
    ai_budget_exempt_user_ids: str = Field(default="", alias="AI_BUDGET_EXEMPT_USER_IDS")
    feature_token_budget_shadow: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "FEATURE_TOKEN_BUDGET_SHADOW", "AIMMS_FEATURE_TOKEN_BUDGET_SHADOW"
        ),
    )
    feature_token_budget_enforce: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_TOKEN_BUDGET_ENFORCE", "AIMMS_FEATURE_TOKEN_BUDGET_ENFORCE"
        ),
    )
    # S12: finite quota profiles (standard/evaluation/service). The flag
    # switches check_budget from the v1 single-cap counter to profile-resolved
    # caps + reservation/settlement accounting; whether a block is REAL stays
    # governed by the token-budget shadow/enforce pair above (no third
    # enforce flag). Enforcement additionally requires the verified shared
    # cache backend (Part-4 admin item) — LocMem is disqualifying.
    feature_ai_quota_profiles: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_AI_QUOTA_PROFILES", "AIMMS_FEATURE_AI_QUOTA_PROFILES"
        ),
    )
    # S13: shared-cache active-turn admission control. Shadow logs
    # admission.would_reject; enforce returns typed 503 ai_capacity_busy.
    # Admission FAILS OPEN on store errors even under enforce (availability
    # protection, not a security boundary — see quota/admission.py ADR).
    feature_ai_admission_control_shadow: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "FEATURE_AI_ADMISSION_CONTROL_SHADOW", "AIMMS_FEATURE_AI_ADMISSION_CONTROL_SHADOW"
        ),
    )
    feature_ai_admission_control_enforce: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_AI_ADMISSION_CONTROL_ENFORCE", "AIMMS_FEATURE_AI_ADMISSION_CONTROL_ENFORCE"
        ),
    )
    # S12/S13 numeric knobs — deliberately NOT in the aimms_flags registry
    # (same decision as AI_USER_DAILY_TOKEN_BUDGET: the registry's closure
    # test governs FEATURE_* names; plain limits live as Settings only).
    ai_quota_policy_cache_ttl_s: int = Field(default=60, ge=1, alias="AI_QUOTA_POLICY_CACHE_TTL_S")
    #: Worst-case per-turn token reservation (configured bound, never p95).
    ai_quota_turn_reserve_tokens: int = Field(
        default=32_000, ge=0, alias="AI_QUOTA_TURN_RESERVE_TOKENS"
    )
    #: Deployment/environment namespace for quota + admission counters, so
    #: shared stores (one Redis serving several envs) never cross-count.
    ai_deployment_env: str = Field(default="default", alias="AI_DEPLOYMENT_ENV")
    # Rate-limit knobs (defaults = the historic literals; deployment env
    # raises them — deliberately NOT registry flags, the numeric-knob
    # precedent). The chat pair governs /chat, /chat/stream, and /agui.
    ai_rate_chat_per_minute: int = Field(default=10, ge=1, alias="AI_RATE_CHAT_PER_MINUTE")
    ai_rate_chat_per_hour: int = Field(default=100, ge=1, alias="AI_RATE_CHAT_PER_HOUR")
    ai_rate_user_per_minute: int = Field(default=20, ge=1, alias="AI_RATE_USER_PER_MINUTE")
    ai_rate_user_per_hour: int = Field(default=200, ge=1, alias="AI_RATE_USER_PER_HOUR")
    ai_rate_global_per_minute: int = Field(default=100, ge=1, alias="AI_RATE_GLOBAL_PER_MINUTE")
    ai_admission_max_active_per_user: int = Field(
        default=2, ge=1, alias="AI_ADMISSION_MAX_ACTIVE_PER_USER"
    )
    #: Pilot operating envelope: five concurrent in-flight turns (§8.9).
    ai_admission_max_active_global: int = Field(
        default=5, ge=1, alias="AI_ADMISSION_MAX_ACTIVE_GLOBAL"
    )
    ai_admission_retry_after_s: int = Field(default=5, ge=1, alias="AI_ADMISSION_RETRY_AFTER_S")
    #: Lease TTL must exceed the hard turn deadline (turn_wall_clock_cap_s,
    #: 240s) plus cleanup grace so a leaked lease self-clears.
    ai_admission_lease_ttl_s: int = Field(default=300, ge=60, alias="AI_ADMISSION_LEASE_TTL_S")
    # S37: deterministic model tiering through one policy table
    # (ai/core/model_policy.py). Shadow logs any legacy-vs-policy divergence
    # (the initial table is the identity, so a divergence means a policy
    # edit); enforce switches callers to the policy choice. The first real
    # policy edit — text lookup-shaped wf8 turns on the fast deployment —
    # sits behind its own flag and flips only after the S39 golden set
    # passes against the fast deployment.
    feature_model_tiering_shadow: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "FEATURE_MODEL_TIERING_SHADOW", "AIMMS_FEATURE_MODEL_TIERING_SHADOW"
        ),
    )
    feature_model_tiering_enforce: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_MODEL_TIERING_ENFORCE", "AIMMS_FEATURE_MODEL_TIERING_ENFORCE"
        ),
    )
    feature_wf8_text_fast_tier: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_WF8_TEXT_FAST_TIER", "AIMMS_FEATURE_WF8_TEXT_FAST_TIER"
        ),
    )
    # S38: typed turn failures. Off = classify-and-log-only (shadow); on =
    # the RUN_ERROR event carries failure_class and the FAILED message uses
    # the per-class localized template.
    feature_typed_turn_failures: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_TYPED_TURN_FAILURES", "AIMMS_FEATURE_TYPED_TURN_FAILURES"
        ),
    )
    # S38: watermarked thread compaction. Shadow summarizes and writes the
    # watermark/summary (DB-observable, behavior-inert while the frontend
    # title guard is deployed); the full flag additionally injects the
    # summary note and truncates injected history at the watermark.
    feature_thread_compaction_shadow: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_THREAD_COMPACTION_SHADOW", "AIMMS_FEATURE_THREAD_COMPACTION_SHADOW"
        ),
    )
    feature_thread_compaction: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_THREAD_COMPACTION", "AIMMS_FEATURE_THREAD_COMPACTION"
        ),
    )
    # M1 PR E (plan §9.3): replay the builder's transcript into the
    # enumerated rails beyond wf8 (rbac_run specialists, the reasoning
    # envelope's conversation section, wf1's first step). Dark until the D5
    # entry baseline is journaled; wf8 is ungated (it always replayed).
    feature_memory_rail_replay: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_MEMORY_RAIL_REPLAY", "AIMMS_FEATURE_MEMORY_RAIL_REPLAY"
        ),
    )
    # S45: real token streaming on the wf8/general fast TEXT rail. Dark by
    # default; the reasoning rail, voice turns, and replay stay single-delta
    # structurally regardless of this flag.
    feature_token_streaming: bool = Field(
        default=False,
        validation_alias=AliasChoices("FEATURE_TOKEN_STREAMING", "AIMMS_FEATURE_TOKEN_STREAMING"),
    )
    # S46: content-free tool-call + step events on the chat stream. Names,
    # ids, status and duration ONLY — arguments and results never leave the
    # server (fault discipline).
    feature_tool_events: bool = Field(
        default=False,
        validation_alias=AliasChoices("FEATURE_TOOL_EVENTS", "AIMMS_FEATURE_TOOL_EVENTS"),
    )
    # S49: the spec-clean AG-UI adapter route. Off ⇒ /agui 404s at request
    # time (indistinguishable from absent); the frontend's auto wire
    # selection keys off the /threads capabilities advertisement.
    feature_agui_endpoint: bool = Field(
        default=False,
        validation_alias=AliasChoices("FEATURE_AGUI_ENDPOINT", "AIMMS_FEATURE_AGUI_ENDPOINT"),
    )
    # S40: NLI groundedness cascade. Dark this phase — the model deps ship in
    # the never-installed ai/requirements-eval.txt and only the offline
    # eval harness exists; live cascade wiring is Phase 8.
    feature_nli_groundedness: bool = Field(
        default=False,
        validation_alias=AliasChoices("FEATURE_NLI_GROUNDEDNESS", "AIMMS_FEATURE_NLI_GROUNDEDNESS"),
    )
    feature_voice_live_diagnosis: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_VOICE_LIVE_DIAGNOSIS", "AIMMS_FEATURE_VOICE_LIVE_DIAGNOSIS"
        ),
    )
    # Safety tightening (Tier-1): restrict voice-modality lookups to read-only tools
    # and a read-only spoken prompt. Defaults on because voice is contractually
    # read-only; set false to revert voice to the full text toolset.
    feature_voice_readonly_tools: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "FEATURE_VOICE_READONLY_TOOLS", "AIMMS_FEATURE_VOICE_READONLY_TOOLS"
        ),
    )
    # Tier-1 latency: answer pattern-matched voice lookups from the deterministic
    # fast path (permission-gated) instead of the LLM tool loop. Off by default.
    feature_voice_fast_path: bool = Field(
        default=False,
        validation_alias=AliasChoices("FEATURE_VOICE_FAST_PATH", "AIMMS_FEATURE_VOICE_FAST_PATH"),
    )
    # Confirmed actions: allow a voice-initiated write ONLY through a mandatory verbal
    # confirmation turn (propose -> exact read-back -> explicit spoken confirm ->
    # execute via the same RBAC-gated write tools text uses). Destructive actions
    # require a strict server-authored phrase. This remains a kill switch: while
    # disabled, the structural read-only fence keeps effect wording advisory only.
    #
    # ON by default: RBAC decides who may write, not the modality. A user with
    # write permissions holds them on every surface, so leaving this off made
    # voice arbitrarily weaker than text chat for the same person.
    #
    # It was forced off as an incident mitigation, because 7779b5720 turned it on
    # while the gate behind it was unsound -- severity was classified from the
    # user's utterance rather than the resolved tool, the read-only fence did not
    # cover the kanban/email tools (they bypass the REST client it lives in), and
    # an injected turn could reach the router before it was refused. Those are the
    # reasons the flag was the safety boundary; all three are now fixed, so the
    # boundary is back where it belongs: permission_profile() + a mandatory
    # confirmation turn. Set FEATURE_VOICE_WRITE_CONFIRMATION=false to re-arm the
    # kill switch for a deployment that wants voice to stay strictly read-only.
    feature_voice_write_confirmation: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "FEATURE_VOICE_WRITE_CONFIRMATION", "AIMMS_FEATURE_VOICE_WRITE_CONFIRMATION"
        ),
    )
    # Ceiling on the voice action planner. It resolves one tool call through an
    # agent loop; past this the turn degrades to the same advisory refusal it
    # would have produced anyway, instead of leaving the speaker in silence.
    voice_write_plan_timeout_s: float = Field(
        default=8.0, ge=1.0, le=60.0, alias="VOICE_WRITE_PLAN_TIMEOUT_S"
    )
    # Prior thread MESSAGES replayed into a lookup turn so a follow-up ("just
    # the ones over 2000") resolves against what was already said. Renamed
    # from chat_history_turns (S24): it always counted messages — 12 messages
    # is 6 real exchanges, which RAISES the old effective window (6 messages
    # = 3 exchanges). Legacy env spellings still load. 0 disables replay.
    chat_history_messages: int = Field(
        default=12,
        validation_alias=AliasChoices(
            "AIMMS_CHAT_HISTORY_MESSAGES",
            "CHAT_HISTORY_MESSAGES",
            "AIMMS_CHAT_HISTORY_TURNS",
            "CHAT_HISTORY_TURNS",
        ),
        ge=0,
        le=50,
    )
    # S24 replay budgets: one huge answer used to be re-sent verbatim for up
    # to the whole window. Per-message head-truncation (with a visible
    # marker) and a total cap that drops OLDEST whole messages — never the
    # newest two, which are the follow-up antecedent. 0 disables either cap.
    chat_history_max_message_chars: int = Field(
        default=4000, ge=0, le=32000, alias="CHAT_HISTORY_MAX_MESSAGE_CHARS"
    )
    chat_history_max_total_chars: int = Field(
        default=24000, ge=0, le=200000, alias="CHAT_HISTORY_MAX_TOTAL_CHARS"
    )
    # S24: persist per-turn provider usage (wf8 + Luna) into terminal turn
    # metadata so budgets are measured, not guessed. Kill switch only.
    feature_turn_usage_persistence: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "FEATURE_TURN_USAGE_PERSISTENCE", "AIMMS_FEATURE_TURN_USAGE_PERSISTENCE"
        ),
    )

    # -------------------------------------------------------------------------
    # S18 turn-loop budgets (A5/A12)
    # -------------------------------------------------------------------------
    # One wall-clock ceiling for a whole text turn. Before it, only a client
    # disconnect ended a hung turn. Generous by design — a tripwire above the
    # observed p99, not a behaviour change; 0 disables. 120s proved too tight
    # in live testing (a legitimate wf2 analysis exceeded it), hence 240s.
    turn_wall_clock_cap_s: float = Field(
        default=240.0, ge=0.0, le=600.0, alias="TURN_WALL_CLOCK_CAP_S"
    )
    # Tighter budget for the routing stage alone: a hung classifier endpoint
    # must not consume the whole turn cap. 0 disables (the turn cap still holds).
    turn_routing_budget_s: float = Field(
        default=30.0, ge=0.0, le=120.0, alias="TURN_ROUTING_BUDGET_S"
    )
    # Per-provider budget inside context gathering (A12): the three providers
    # run concurrently and one hung provider costs at most this much.
    context_provider_timeout_s: float = Field(
        default=5.0, ge=0.5, le=60.0, alias="CONTEXT_PROVIDER_TIMEOUT_S"
    )
    # wf3's internal fan-out budget. The old hardcoded 30s left ZERO research
    # agents complete under real load, so even the salvage path had nothing to
    # keep; 90s sits under the turn cap with room for synthesis.
    research_stage_timeout_s: float = Field(
        default=90.0, ge=10.0, le=300.0, alias="RESEARCH_STAGE_TIMEOUT_S"
    )

    # -------------------------------------------------------------------------
    # WS3 Foundry reasoning adapter
    # -------------------------------------------------------------------------
    # The owner-selected primary path references a pinned Foundry project agent.
    # Explicit aliases retain the deployment's AZURE_* setting names while the
    # rest of this settings object continues to use its AIMMS_ prefix.
    # Prompt of record is the in-repo _DEVELOPER_INSTRUCTIONS (git-reviewed), so
    # the reasoning path defaults to direct_deployment rather than the external
    # Foundry portal agent. Set agent_reference to pin a Foundry agent instead.
    azure_voice_reasoning_invocation_mode: Literal["agent_reference", "direct_deployment"] = Field(
        default="direct_deployment", alias="AZURE_VOICE_REASONING_INVOCATION_MODE"
    )
    azure_foundry_project_endpoint: str = Field(
        default="https://aimms-foundry.services.ai.azure.com/api/projects/Epcon-AIMMS",
        alias="AZURE_FOUNDRY_PROJECT_ENDPOINT",
    )
    azure_voice_agent_name: str = Field(default="voice-agent-test", alias="AZURE_VOICE_AGENT_NAME")
    azure_voice_agent_version: str = Field(default="3", alias="AZURE_VOICE_AGENT_VERSION")
    azure_luna_endpoint: str = Field(default="", alias="AZURE_LUNA_ENDPOINT")
    azure_luna_deployment: str = Field(default="gpt-5.6-luna", alias="AZURE_LUNA_DEPLOYMENT")
    azure_luna_api_version: str = Field(
        default="2025-04-01-preview", alias="AZURE_LUNA_API_VERSION"
    )
    azure_luna_reasoning_effort: Literal["low", "medium", "high"] = Field(
        default="medium", alias="AZURE_LUNA_REASONING_EFFORT"
    )
    azure_luna_diagnosis_max_tool_rounds: int = Field(
        default=6,
        ge=1,
        le=12,
        alias="AZURE_LUNA_DIAGNOSIS_MAX_TOOL_ROUNDS",
    )
    azure_luna_diagnosis_timeout_s: float = Field(
        default=30.0,
        gt=0,
        le=120,
        alias="AZURE_LUNA_DIAGNOSIS_TIMEOUT_S",
    )
    # Voice turns carry ASR + session overhead on the same wall clock, so the
    # tool loop that completes a text turn at ~40 s blows a shared 45 s bound
    # on voice (Phase 6 battery A3). Voice gets its own ceiling; the spoken
    # "Let me check that" interim keeps the technician informed meanwhile.
    azure_luna_diagnosis_timeout_voice_s: float = Field(
        default=75.0,
        gt=0,
        le=120,
        alias="AZURE_LUNA_DIAGNOSIS_TIMEOUT_VOICE_S",
    )
    azure_luna_diagnosis_max_output_tokens: int = Field(
        default=6000,
        ge=128,
        le=16000,
        alias="AZURE_LUNA_DIAGNOSIS_MAX_OUTPUT_TOKENS",
    )
    azure_luna_diagnosis_max_tool_data_kb: int = Field(
        default=256,
        ge=1,
        le=1024,
        alias="AZURE_LUNA_DIAGNOSIS_MAX_TOOL_DATA_KB",
    )
    # S28: server-observed entity chips under answers. Additive and inert on
    # stale clients (unknown STATE_DELTA kinds are ignored), so it defaults
    # on; the flag is a kill switch only.
    feature_entity_manifest: bool = Field(
        default=True,
        validation_alias=AliasChoices("FEATURE_ENTITY_MANIFEST", "AIMMS_FEATURE_ENTITY_MANIFEST"),
    )
    # R4 media-evidence chips: additive + inert on stale clients, so the
    # default is on and the flag is purely a kill switch (S28 posture).
    feature_media_evidence: bool = Field(
        default=True,
        validation_alias=AliasChoices("FEATURE_MEDIA_EVIDENCE", "AIMMS_FEATURE_MEDIA_EVIDENCE"),
    )
    # S27: cite-or-downgrade for manuals-grounded wf8 answers. shadow logs a
    # content-free would_downgrade line and persists the assessment; enforce
    # replaces confirmed-ungrounded answers with the downgrade template.
    # Enforce flip is HUMAN-gated after a >=5-day shadow soak (plan L784).
    manual_grounding_mode: Literal["off", "shadow", "enforce"] = Field(
        default="shadow",
        validation_alias=AliasChoices("AIMMS_MANUAL_GROUNDING_MODE", "MANUAL_GROUNDING_MODE"),
    )
    # S10: the evidence-analysis gate. off keeps the ANALYSIS route abstaining
    # byte-identically; shadow runs the deterministic closure scans over
    # legacy wf8 ANALYSIS-intent answers (persisted soak blob) and
    # dark-rehearses the full executor when the analysis route is reached;
    # enforce serves the validated v2 response. Serving v2 additionally
    # requires FEATURE_AI_ANALYSIS_ROUTER_ENFORCE; both flips are HUMAN-gated
    # after the shadow soak review.
    evidence_gate_mode: Literal["off", "shadow", "enforce"] = Field(
        default="shadow",
        validation_alias=AliasChoices("AIMMS_EVIDENCE_GATE_MODE", "EVIDENCE_GATE_MODE"),
    )
    # S15/§15.4: arm the fail-closed pilot-stop admission gate. Dark by
    # default; the latch state, set/clear commands, and reports all work
    # regardless — only the per-turn check is gated. Deliberately the
    # INVERSE of admission control's fail-open ADR: with the flag on, an
    # unreadable latch refuses new turns (pilot_latch_unavailable).
    feature_ai_pilot_stop_latch: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_AI_PILOT_STOP_LATCH", "AIMMS_FEATURE_AI_PILOT_STOP_LATCH"
        ),
    )
    # S16/Q48: the Django plane runs the purge jobs; this mirror exists for
    # both-plane registry parity only. Dark by owner decision (2026-08-29):
    # data is kept — nothing forces these jobs on.
    feature_ai_retention_jobs: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_AI_RETENTION_JOBS", "AIMMS_FEATURE_AI_RETENTION_JOBS"
        ),
    )
    # Bounded staleness for the cached latch read; engage_latch writes the
    # cache directly, so automatic stops propagate near-instantly and this
    # TTL only bounds MANUAL DB edits' propagation.
    ai_pilot_latch_cache_ttl_s: int = Field(
        default=10, ge=1, le=300, alias="AI_PILOT_LATCH_CACHE_TTL_S"
    )
    # Deliberately NOT in the flag REGISTRY (AI_USER_DAILY_TOKEN_BUDGET
    # precedent): numeric tuning knobs, not feature flags. Defaults follow
    # the §8.9 SLO table — synthesis-class p95 for the turn, p50 for the
    # single synthesis call — under the 240s hard turn cap.
    analysis_turn_deadline_s: float = Field(
        default=45.0, ge=1.0, le=240.0, alias="ANALYSIS_TURN_DEADLINE_S"
    )
    analysis_synthesis_timeout_s: float = Field(
        default=20.0, ge=1.0, le=120.0, alias="ANALYSIS_SYNTHESIS_TIMEOUT_S"
    )
    # S26: when a medium/high-confidence diagnosis cites no history and never
    # consulted the history tools, the server retrieves history evidence and
    # runs ONE stateless full-transcript continuation. Dark by default; flip
    # dev -> experimental, and raise AZURE_LUNA_DIAGNOSIS_TIMEOUT_S to 45
    # wherever this is enabled so the continuation fits the deadline.
    feature_history_enrichment: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_HISTORY_ENRICHMENT", "AIMMS_FEATURE_HISTORY_ENRICHMENT"
        ),
    )
    # S32b (B6): read-only thread sharing via explicit audited grants. Dark by
    # default; the same env var is bridged into Django settings so the
    # repository (Django plane) and the /threads surface (this plane) can
    # never disagree about whether sharing exists.
    feature_thread_sharing: bool = Field(
        default=False,
        validation_alias=AliasChoices("FEATURE_THREAD_SHARING", "AIMMS_FEATURE_THREAD_SHARING"),
    )
    # S33 (B7): read-only voice step-through of applied guided procedures.
    # Verbatim step_snapshot text only; completion posts through the existing
    # step-execution command API as the user. Dark by default.
    feature_guided_procedures: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_GUIDED_PROCEDURES", "AIMMS_FEATURE_GUIDED_PROCEDURES"
        ),
    )
    azure_luna_history_enrichment_rounds: int = Field(
        default=1,
        ge=0,
        le=2,
        alias="AZURE_LUNA_HISTORY_ENRICHMENT_ROUNDS",
    )
    repair_safety_p0s_closed: bool = Field(default=False, alias="REPAIR_SAFETY_P0S_CLOSED")
    diagnostic_capability_resolver: str = ""

    @field_validator(
        "azure_foundry_project_endpoint",
        "azure_voice_agent_name",
        "azure_voice_agent_version",
        "azure_luna_endpoint",
        "azure_luna_deployment",
        "azure_luna_api_version",
        "diagnostic_capability_resolver",
        mode="after",
    )
    @classmethod
    def normalize_reasoning_setting(cls, value: str) -> str:
        """Trim provider identifiers without ever resolving a floating version."""
        return value.strip()

    @model_validator(mode="after")
    def validate_reasoning_provider(self) -> "Settings":
        """Fail closed for an incomplete or floating provider configuration."""
        if self.azure_voice_reasoning_invocation_mode == "agent_reference":
            if not self.azure_foundry_project_endpoint.startswith("https://"):
                raise ValueError("AZURE_FOUNDRY_PROJECT_ENDPOINT must use HTTPS")
            if not self.azure_voice_agent_name:
                raise ValueError("AZURE_VOICE_AGENT_NAME is required")
            version = self.azure_voice_agent_version.lower()
            if not version or version == "latest":
                raise ValueError("AZURE_VOICE_AGENT_VERSION must be pinned")
        elif self.feature_voice_live_diagnosis and not (
            self.azure_luna_endpoint or self.azure_openai_endpoint
        ):
            # Only required when the reasoning path is actually enabled;
            # diagnosis remains feature-gated off by default.
            raise ValueError(
                "AZURE_LUNA_ENDPOINT or AZURE_OPENAI_ENDPOINT is required for "
                "direct deployment invocation"
            )
        return self

    # -------------------------------------------------------------------------
    # WS4 Voice Live transport (all fail closed; flags default off)
    # -------------------------------------------------------------------------
    feature_voice_live: bool = Field(
        default=False,
        validation_alias=AliasChoices("FEATURE_VOICE_LIVE", "AIMMS_FEATURE_VOICE_LIVE"),
    )
    feature_voice_live_webrtc: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "FEATURE_VOICE_LIVE_WEBRTC", "AIMMS_FEATURE_VOICE_LIVE_WEBRTC"
        ),
    )
    feature_voice_live_relay: bool = Field(
        default=False,
        validation_alias=AliasChoices("FEATURE_VOICE_LIVE_RELAY", "AIMMS_FEATURE_VOICE_LIVE_RELAY"),
    )
    # Phase 5 (optional): move the transport to a native realtime model
    # (gpt-realtime) with native semantic VAD for snappier turn-taking. Answers
    # stay governed -- create_response:false, exact-TTS, no session tools, prompt
    # of record in the repo -- so this is a transport swap, never A4/A5. Off by
    # default; requires FEATURE_VOICE_LIVE and a gpt-realtime session model.
    feature_voice_native_sts: bool = Field(
        default=False,
        validation_alias=AliasChoices("FEATURE_VOICE_NATIVE_STS", "AIMMS_FEATURE_VOICE_NATIVE_STS"),
    )
    azure_voicelive_endpoint: str = Field(default="", alias="AZURE_VOICELIVE_ENDPOINT")
    azure_voicelive_model: str = Field(default="gpt-4.1-mini", alias="AZURE_VOICELIVE_MODEL")
    azure_voicelive_api_version: str = Field(
        default="2026-04-10", alias="AZURE_VOICELIVE_API_VERSION"
    )
    azure_voicelive_webrtc_api_version: str = Field(
        default="2026-01-01-preview", alias="AZURE_VOICELIVE_WEBRTC_API_VERSION"
    )
    azure_voicelive_voice: str = Field(default="en-US-AvaNeural", alias="AZURE_VOICELIVE_VOICE")
    azure_voicelive_language: str = Field(default="en-US", alias="AZURE_VOICELIVE_LANGUAGE")
    azure_voicelive_transcription_model: str = Field(
        default="azure-speech", alias="AZURE_VOICELIVE_TRANSCRIPTION_MODEL"
    )
    azure_voicelive_phrase_hints: list[str] = Field(
        default_factory=list, alias="AZURE_VOICELIVE_PHRASE_HINTS"
    )
    voice_live_max_active_sessions_per_user: int = Field(
        default=1, ge=1, le=5, alias="VOICE_LIVE_MAX_ACTIVE_SESSIONS_PER_USER"
    )
    voice_live_idle_timeout_s: int = Field(
        default=300, ge=30, le=3600, alias="VOICE_LIVE_IDLE_TIMEOUT_S"
    )
    voice_live_max_session_age_s: int = Field(
        default=3600, ge=60, le=14400, alias="VOICE_LIVE_MAX_SESSION_AGE_S"
    )
    voice_live_max_turns_per_session: int = Field(
        default=100, ge=1, le=1000, alias="VOICE_LIVE_MAX_TURNS_PER_SESSION"
    )
    # Privacy invariant, not a rollout option: typed Literal[False] makes any
    # attempt to enable raw realtime audio retention a startup failure.
    voice_live_store_raw_audio: Literal[False] = Field(
        default=False, alias="VOICE_LIVE_STORE_RAW_AUDIO"
    )
    # WS5 critical-term policy: transcripts below this ASR confidence are held
    # for confirmation. A transcript with NO confidence value is not held —
    # providers that omit the field would otherwise hold every utterance —
    # and the server routing default matches (absent -> 1.0). Served to the
    # client via the capability probe so there is one source of truth.
    voice_confidence_floor: float = Field(
        default=0.85, ge=0.0, le=1.0, alias="AIMMS_VOICE_CONFIDENCE_FLOOR"
    )

    @model_validator(mode="after")
    def validate_voice_live_transport(self) -> "Settings":
        """Fail closed when realtime voice is enabled with an unusable transport."""
        if self.feature_voice_live_webrtc and not self.feature_voice_live:
            raise ValueError("FEATURE_VOICE_LIVE_WEBRTC requires FEATURE_VOICE_LIVE")
        if self.feature_voice_live_relay and not self.feature_voice_live:
            raise ValueError("FEATURE_VOICE_LIVE_RELAY requires FEATURE_VOICE_LIVE")
        if self.feature_voice_native_sts and not self.feature_voice_live:
            raise ValueError("FEATURE_VOICE_NATIVE_STS requires FEATURE_VOICE_LIVE")
        if not self.feature_voice_live:
            return self
        from ai.core.voice.endpoints import (
            VoiceLiveEndpointError,
            build_control_url,
        )

        try:
            build_control_url(
                self.azure_voicelive_endpoint,
                self.azure_voicelive_model,
                api_version=self.azure_voicelive_api_version,
            )
        except VoiceLiveEndpointError as exc:
            raise ValueError(f"Voice Live transport configuration is invalid: {exc}") from exc
        session_model = self.azure_voicelive_model.strip().lower()
        if (
            session_model.startswith("gpt-realtime")
            and self.azure_voicelive_transcription_model.strip().lower() == "azure-speech"
        ):
            raise ValueError(
                "azure-speech transcription cannot be paired with a gpt-realtime session model"
            )
        if self.feature_voice_native_sts and not session_model.startswith("gpt-realtime"):
            # The native transport is a realtime model; the azure-speech guard
            # above then forces a realtime-compatible transcription model.
            raise ValueError(
                "FEATURE_VOICE_NATIVE_STS requires a gpt-realtime session model "
                "(set AZURE_VOICELIVE_MODEL=gpt-realtime)"
            )
        return self

    # -------------------------------------------------------------------------
    # CORS Configuration
    # -------------------------------------------------------------------------
    cors_allowed_origins: list[str] = Field(
        default=[
            "http://localhost:5173",  # Vite dev server
            "http://localhost:3000",  # Alternative dev server
            "http://localhost:8000",  # InvenTree backend (same-origin proxy)
            "http://127.0.0.1:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:8000",
        ],
        alias="CORS_ALLOWED_ORIGINS",
    )
    cors_allow_credentials: bool = Field(default=True, alias="CORS_ALLOW_CREDENTIALS")

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        """Parse comma-separated origins from env var."""
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    # -------------------------------------------------------------------------
    # Demo Dataset Configuration (for testing)
    # -------------------------------------------------------------------------
    demo_dataset_path: Path = Field(
        default=Path("./inventree-demo-dataset"), alias="DEMO_DATASET_PATH"
    )
    demo_dataset_json: Path = Field(
        default=Path("./inventree-demo-dataset/inventree_data.json"), alias="DEMO_DATASET_JSON"
    )
    use_demo_dataset: bool = Field(default=False, alias="USE_DEMO_DATASET")

    # -------------------------------------------------------------------------
    # Azure OpenAI (convenience accessors - reads from AZURE_OPENAI_* env vars)
    # -------------------------------------------------------------------------
    azure_openai_endpoint: str = Field(default="", alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_api_key: str = Field(default="", alias="AZURE_OPENAI_API_KEY")
    azure_openai_deployment: str = Field(default="gpt-4o", alias="AZURE_OPENAI_DEPLOYMENT")
    azure_openai_fast_deployment: str = Field(
        default="gpt-4o-mini", alias="AZURE_OPENAI_FAST_DEPLOYMENT"
    )
    # D-10 / CR-2: SUMMARIZATION and EXTRACTION never resolve to the fast
    # tier. Empty -> azure_openai_deployment (standard). An env field rather
    # than a Django setting because the workers that run ``_summarize`` are
    # configured by container env; its VALUE is the DataZone deployment
    # (gpt-5.6-luna-dz), folding the former DATAZONE key into one knob.
    azure_openai_summarization_deployment: str = Field(
        default="", alias="AZURE_OPENAI_SUMMARIZATION_DEPLOYMENT"
    )
    # Sent as ``reasoning_effort`` ONLY when the override above is set: the
    # override is a reasoning deployment by decision, while the gpt-4.x
    # standard tiers reject the parameter. Precedent:
    # ``azure_luna_reasoning_effort``.
    azure_summarization_reasoning_effort: Literal["low", "medium", "high"] = Field(
        default="low", alias="AZURE_SUMMARIZATION_REASONING_EFFORT"
    )
    # M1 (plan §9.9 / GR-33): deployments allowed to receive ``prompt_cache_key``
    # via ``additional_chat_options``. Comma-separated; empty = dark (an
    # unsupported parameter 400s the turn, so this ships off until the B8
    # probe passes on the deployment).
    aimms_prompt_cache_key_deployments: str = Field(
        default="", alias="AIMMS_PROMPT_CACHE_KEY_DEPLOYMENTS"
    )
    azure_openai_embedding_deployment: str = Field(
        # Must agree with controlled_document_embedding_dimensions below: the
        # live index stores 3072-dimension text-embedding-3-large vectors. The
        # old ada-002 default (1536) contradicted that and failed per query on
        # any environment that forgot the env override (S17 A4).
        default="text-embedding-3-large",
        # Canonical name first; the plural spelling has shipped in real .env
        # files and silently not loading a credential is worse than an alias.
        validation_alias=AliasChoices(
            "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT"
        ),
    )
    azure_openai_api_version: str = Field(default="2024-10-21", alias="AZURE_OPENAI_API_VERSION")

    # -------------------------------------------------------------------------
    # S17 model pins and boot probes (fail closed; each has its own kill switch)
    # -------------------------------------------------------------------------
    # A4: at startup, embed one known string and refuse to boot when the vector
    # dimensions cannot be stored in the configured controlled-document index.
    # Skips loudly when the embedding plane or the index is unconfigured.
    embedding_boot_probe_enabled: bool = Field(default=True, alias="EMBEDDING_BOOT_PROBE_ENABLED")
    # A10: resolve each chat deployment's actual model identity at boot with a
    # one-token call and assert it against the pins below. Opt-in: every
    # replica boot pays two provider calls, so a deployment enables it
    # deliberately rather than by default.
    model_version_boot_probe_enabled: bool = Field(
        default=False, alias="MODEL_VERSION_BOOT_PROBE_ENABLED"
    )
    # Optional pins: empty string means record-only (the resolved identity is
    # still logged and stamped onto turns); a set pin makes a mismatch fatal.
    azure_openai_expected_model: str = Field(default="", alias="AZURE_OPENAI_EXPECTED_MODEL")
    azure_openai_expected_fast_model: str = Field(
        default="", alias="AZURE_OPENAI_EXPECTED_FAST_MODEL"
    )
    azure_openai_expected_embedding_model: str = Field(
        default="", alias="AZURE_OPENAI_EXPECTED_EMBEDDING_MODEL"
    )

    # -------------------------------------------------------------------------
    # Azure AI Search Configuration
    # -------------------------------------------------------------------------
    azure_search_endpoint: str = Field(default="", alias="AZURE_SEARCH_ENDPOINT")
    azure_search_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("AZURE_SEARCH_API_KEY", "AZURE_SEARCH_KEY"),
    )
    # Retired at R5 with search_part_documents: no code reads this index any
    # more. The field survives ONLY as a reserved alias name -- the distinctness
    # validator below and the projections' alias guards still consult it so a
    # stale env setting can never let auto-ingested content alias a legacy index.
    azure_search_documents_index: str = Field(default="", alias="AZURE_SEARCH_DOCUMENTS_INDEX")
    azure_search_controlled_documents_index: str = Field(
        default="", alias="AZURE_SEARCH_CONTROLLED_DOCUMENTS_INDEX"
    )
    controlled_documents_root: Path = Field(
        default=Path("/home/inventree/data/media/ai/controlled-documents"),
        alias="CONTROLLED_DOCUMENTS_ROOT",
    )
    controlled_document_embedding_dimensions: int = Field(
        default=3072,
        ge=1,
        alias="CONTROLLED_DOCUMENT_EMBEDDING_DIMENSIONS",
    )

    # -------------------------------------------------------------------------
    # Attachment RAG (R0): auto-ingested uploads, two embedding spaces.
    # Text space = Cohere Embed v4 (Azure Foundry serverless); media space =
    # Gemini Embedding 2 (Vertex AI). ON by default since R5 (posture C):
    # a DEFAULTED flag quietly degrades to False when its plane's providers
    # are incomplete, while an EXPLICITLY SET truthy flag still fails closed
    # at startup — the operator invariant survives, and provider-less forks
    # and CI construct Settings without providers. The governed
    # controlled-document corpus (text-embedding-3-large) is separate and
    # untouched -- the alias guard below keeps the indexes distinct.
    # -------------------------------------------------------------------------
    feature_attachment_rag_ingest: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "FEATURE_ATTACHMENT_RAG_INGEST", "AIMMS_FEATURE_ATTACHMENT_RAG_INGEST"
        ),
    )
    feature_attachment_rag_retrieval: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "FEATURE_ATTACHMENT_RAG_RETRIEVAL", "AIMMS_FEATURE_ATTACHMENT_RAG_RETRIEVAL"
        ),
    )
    feature_media_rag_ingest: bool = Field(
        default=True,
        validation_alias=AliasChoices("FEATURE_MEDIA_RAG_INGEST", "AIMMS_FEATURE_MEDIA_RAG_INGEST"),
    )
    feature_media_rag_retrieval: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "FEATURE_MEDIA_RAG_RETRIEVAL", "AIMMS_FEATURE_MEDIA_RAG_RETRIEVAL"
        ),
    )
    azure_search_attachment_docs_index: str = Field(
        default="aimms-attachment-docs-v1", alias="AZURE_SEARCH_ATTACHMENT_DOCS_INDEX"
    )
    azure_search_media_index: str = Field(
        default="aimms-media-evidence-v1", alias="AZURE_SEARCH_MEDIA_INDEX"
    )
    cohere_embed_endpoint: str = Field(default="", alias="COHERE_EMBED_ENDPOINT")
    cohere_embed_key: str = Field(
        default="",
        validation_alias=AliasChoices("COHERE_EMBED_KEY", "COHERE_EMBED_API_KEY"),
    )
    cohere_embed_model: str = Field(default="embed-v-4-0", alias="COHERE_EMBED_MODEL")
    cohere_embed_dimensions: int = Field(default=1536, ge=1, alias="COHERE_EMBED_DIMENSIONS")
    # R5 WP-A. The retired azure-ai-inference SDK hardcoded this; exposing it is
    # the lever for the risk that actually bites -- Azure retiring the
    # api-version, which on a dead SDK would have meant forking it.
    cohere_embed_api_version: str = Field(
        default="2024-05-01-preview", alias="COHERE_EMBED_API_VERSION"
    )
    gcp_project_id: str = Field(default="", alias="GCP_PROJECT_ID")
    gcp_location: str = Field(default="", alias="GCP_LOCATION")
    # wif = Workload Identity Federation external_account JSON (keyless, prod);
    # sa_key = service-account key file (local dev only).
    gcp_auth_mode: Literal["wif", "sa_key"] = Field(default="wif", alias="GCP_AUTH_MODE")
    gcp_credentials_path: str = Field(default="", alias="GCP_CREDENTIALS_PATH")
    # Decision #17: pinned to the GA ID (GA 2026-04-22) before any media
    # backfill; the boot probe + pre-flip smoke validate reachability and the
    # 3072 width live, and fail closed on drift.
    gemini_embed_model: str = Field(default="gemini-embedding-2", alias="GEMINI_EMBED_MODEL")
    gemini_embed_dimensions: int = Field(default=3072, ge=1, alias="GEMINI_EMBED_DIMENSIONS")
    # R5 WP-2a. Asymmetric query/document conditioning for the media space.
    # "off" reproduces R4 exactly. Which of the other two is real is a live
    # question the WP-0a probe answers: the SDK will send ``taskType`` either
    # way, but only the service decides whether it is honoured or ignored, and
    # a rejection would fail the media boot probe on all four apps. Leave this
    # "off" until the probe's cosine block says otherwise.
    gemini_embed_task_conditioning: Literal["off", "task_type", "prefix"] = Field(
        default="off", alias="GEMINI_EMBED_TASK_CONDITIONING"
    )
    # R5 WP-2b. Vertex-only: fuses the clip's audio track into the vector.
    # Clips already carry audio (``cut_segment`` maps ``0:a:0?``), so today the
    # narration is uploaded on every call and used by nothing. Budget at 60 s /
    # 1 FPS: 66*60 video + 25*60 audio + 10*60 timestamps = 6060 of 8192.
    gemini_audio_track_extraction: bool = Field(
        default=False, alias="GEMINI_AUDIO_TRACK_EXTRACTION"
    )
    # Tri-state on purpose: None omits the field entirely (provider default,
    # i.e. silent truncation), False turns an over-budget clip into a loud
    # INVALID_ARGUMENT. Never send it accidentally.
    gemini_auto_truncate: bool | None = Field(default=None, alias="GEMINI_AUTO_TRUNCATE")
    # R5 WP-2c. Frames captioned per video window. 1 reproduces R4 exactly and
    # describes 0.056% of a 60 s window, so a short action is usually invisible
    # to the BM25 leg. Frames ride in ONE vision call (~85 tokens each at
    # detail:"low"), keeping provider calls per window at 2 and the fence
    # heartbeat cadence unchanged.
    rag_video_caption_frames: int = Field(default=1, ge=1, le=10, alias="RAG_VIDEO_CAPTION_FRAMES")
    # R5 WP-F: ±N adjacent-segment expansion on video evidence hits. Capped at
    # 1 — the band arithmetic in media_corpus is provably sufficient only for
    # the immediate neighbours.
    rag_media_adjacent_segments: int = Field(
        default=1, ge=0, le=1, alias="RAG_MEDIA_ADJACENT_SEGMENTS"
    )
    rag_max_doc_mb: int = Field(default=50, ge=1, alias="RAG_MAX_DOC_MB")
    rag_max_image_mb: int = Field(default=25, ge=1, alias="RAG_MAX_IMAGE_MB")
    rag_max_video_mb: int = Field(default=500, ge=1, alias="RAG_MAX_VIDEO_MB")
    # Gemini Embedding 2 accepts clips of at most 120 s per call.
    rag_video_segment_s: int = Field(default=60, ge=10, le=120, alias="RAG_VIDEO_SEGMENT_S")
    rag_video_overlap_s: int = Field(default=5, ge=0, alias="RAG_VIDEO_OVERLAP_S")
    # Bounds worst-case ingest wall clock and provider calls: segments =
    # 1 + floor((D - overlap)/(segment - overlap)); 900 s -> 17 segments at
    # defaults. A duration cap (not a segment-count cap) keeps the invariant
    # "index the whole video or skip it" — a partially indexed video would
    # make a retrieval miss indistinguishable from absence.
    rag_video_max_duration_s: int = Field(
        default=900, ge=120, le=900, alias="RAG_VIDEO_MAX_DURATION_S"
    )
    # In-flight ingest rows older than this are claimable again (stale-worker
    # takeover) and eligible for the resume sweep. Invariant: keep this ABOVE
    # the django-q per-task timeout + cluster retry (stock 90+300; the
    # recommended 600s timeout floors retry at 720 -> 1320), or a takeover can
    # race a still-alive worker.
    rag_stale_claim_s: int = Field(default=1800, ge=600, alias="RAG_STALE_CLAIM_S")

    @field_validator(
        "azure_search_attachment_docs_index",
        "azure_search_media_index",
        # The governed/legacy index names participate in the alias guards, so
        # they must be stripped too — a padded governed name must not let the
        # auto-ingested corpus alias it (review finding F-01).
        "azure_search_controlled_documents_index",
        "azure_search_documents_index",
        "cohere_embed_endpoint",
        "cohere_embed_model",
        "gcp_project_id",
        "gcp_location",
        "gcp_credentials_path",
        "gemini_embed_model",
        mode="after",
    )
    @classmethod
    def normalize_attachment_rag_setting(cls, value: str) -> str:
        """Trim attachment-RAG identifiers; whitespace must not defeat the guards."""
        return value.strip()

    def rag_flag_degraded(self, field: str) -> bool:
        """True when a DEFAULTED RAG flag was degraded off for missing providers."""
        return field in self.__dict__.get("_rag_degraded", ())

    def _attachment_providers_complete(self) -> bool:
        if not self.cohere_embed_endpoint.startswith("https://"):
            return False
        if not self.cohere_embed_model:
            return False
        tail = self.cohere_embed_endpoint.split("://", 1)[-1]
        if "/embeddings" in tail or "/openai" in tail:
            return False
        return bool(self.azure_search_endpoint and self.azure_search_attachment_docs_index)

    def _media_providers_complete(self, *, ingest: bool) -> bool:
        if not (self.gcp_project_id and self.gcp_location and self.gcp_credentials_path):
            return False
        if not self.gemini_embed_model:
            return False
        if not (self.azure_search_endpoint and self.azure_search_media_index):
            return False
        # gpt-4o captions: an ingest-lit plane without the endpoint would
        # fail every image ingest at runtime.
        return not (ingest and not self.azure_openai_endpoint)

    @model_validator(mode="after")
    def validate_attachment_rag(self) -> "Settings":
        """Fail closed on incomplete providers or index aliasing across trust tiers.

        R5 posture C runs FIRST: each RAG flag that is True purely by DEFAULT
        (not in ``model_fields_set`` — env-sourced values, true or false, are
        always in the set) degrades to False when its plane's providers are
        incomplete. Everything after this block — the provider raises and the
        R5 knob-coherence clauses — reads post-degrade values, so an
        explicitly set flag keeps the original fail-closed behavior verbatim.
        This degrade is also load-bearing for CI: config constructs a module-
        level Settings() with no providers at import time.
        """
        degraded: list[str] = []
        attachment_ok = self._attachment_providers_complete()
        for field in ("feature_attachment_rag_ingest", "feature_attachment_rag_retrieval"):
            if getattr(self, field) and field not in self.model_fields_set and not attachment_ok:
                # Plain assignment would add the field to the very set we
                # consult; __setattr__ on the dict keeps "defaulted" intact.
                object.__setattr__(self, field, False)
                degraded.append(field)
        for field, needs_ingest in (
            ("feature_media_rag_ingest", True),
            ("feature_media_rag_retrieval", False),
        ):
            if (
                getattr(self, field)
                and field not in self.model_fields_set
                and not self._media_providers_complete(ingest=needs_ingest)
            ):
                object.__setattr__(self, field, False)
                degraded.append(field)
        if degraded:
            self.__dict__["_rag_degraded"] = tuple(degraded)
            # One bounded, value-free line: names only, never configuration.
            logging.getLogger(__name__).warning(
                "RAG default-on flags degraded off (providers incomplete): %s",
                ",".join(degraded),
            )
        rag_indexes = [
            name
            for name in (
                self.azure_search_attachment_docs_index,
                self.azure_search_media_index,
                self.azure_search_controlled_documents_index,
                self.azure_search_documents_index,
            )
            if name
        ]
        if len(rag_indexes) != len(set(rag_indexes)):
            raise ValueError(
                "Attachment/media/controlled Search indexes must be distinct: "
                "auto-ingested content may never alias a governed index"
            )
        if self.rag_video_overlap_s >= self.rag_video_segment_s:
            raise ValueError("RAG_VIDEO_OVERLAP_S must be smaller than RAG_VIDEO_SEGMENT_S")
        if self.feature_attachment_rag_ingest or self.feature_attachment_rag_retrieval:
            if not self.cohere_embed_endpoint.startswith("https://"):
                raise ValueError(
                    "COHERE_EMBED_ENDPOINT (https) is required when attachment RAG is enabled"
                )
            if not self.cohere_embed_model:
                raise ValueError("COHERE_EMBED_MODEL is required when attachment RAG is enabled")
            # The client builds the route itself, so a path on the endpoint
            # yields /embeddings/embeddings. Deliberately not a host allowlist:
            # that would hard-code Azure topology into config validation.
            tail = self.cohere_embed_endpoint.split("://", 1)[-1]
            if "/embeddings" in tail or "/openai" in tail:
                raise ValueError(
                    "COHERE_EMBED_ENDPOINT must be the resource root, without an API path"
                )
            if not self.azure_search_endpoint or not self.azure_search_attachment_docs_index:
                raise ValueError(
                    "AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_ATTACHMENT_DOCS_INDEX are "
                    "required when attachment RAG is enabled"
                )
        if self.feature_media_rag_ingest or self.feature_media_rag_retrieval:
            if not self.gcp_project_id or not self.gcp_location:
                raise ValueError(
                    "GCP_PROJECT_ID and GCP_LOCATION are required when media RAG is enabled"
                )
            if not self.gcp_credentials_path:
                raise ValueError(
                    "GCP_CREDENTIALS_PATH is required when media RAG is enabled "
                    "(WIF external_account JSON or a local-dev service-account key)"
                )
            if not self.gemini_embed_model:
                raise ValueError("GEMINI_EMBED_MODEL is required when media RAG is enabled")
            if not self.azure_search_endpoint or not self.azure_search_media_index:
                raise ValueError(
                    "AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_MEDIA_INDEX are required "
                    "when media RAG is enabled"
                )
        if self.feature_media_rag_ingest and not self.azure_openai_endpoint:
            # The image path hard-depends on gpt-4o captions; flag-on without
            # the endpoint would fail every ingest at runtime instead of at
            # boot (fail-closed-at-startup contract). Retrieval alone does
            # not caption, so only the ingest flag binds. DI is validated in
            # the aichat boot probe (its settings live in a separate class).
            raise ValueError(
                "AZURE_OPENAI_ENDPOINT is required when media RAG ingest is "
                "enabled (image captions)"
            )
        # --- R5 corpus-affecting knobs -------------------------------------
        # Guard the F1 trap: on the Vertex PREDICT dispatch path the SDK
        # silently DROPS audio_track_extraction, so a deployment could believe
        # audio was fused when nothing was sent. Mirror the SDK's own routing
        # rule locally and refuse the combination outright rather than ship a
        # setting that is a no-op. (t_is_vertex_embed_content_model: any
        # "gemini" model except gemini-embedding-001.)
        media_lit = self.feature_media_rag_ingest or self.feature_media_rag_retrieval
        audio_requested = (
            self.gemini_audio_track_extraction or self.gemini_auto_truncate is not None
        )
        if audio_requested:
            if not media_lit:
                raise ValueError(
                    "GEMINI_AUDIO_TRACK_EXTRACTION/GEMINI_AUTO_TRUNCATE require "
                    "media RAG ingest or retrieval to be enabled"
                )
            pin = self.gemini_embed_model.lower()
            if "gemini" not in pin or pin == "gemini-embedding-001":
                raise ValueError(
                    "GEMINI_AUDIO_TRACK_EXTRACTION requires an embed_content-routed "
                    "Gemini model; the configured pin would silently drop it"
                )
        if self.gemini_audio_track_extraction and self.rag_video_segment_s > 60:
            # 60 s costs 6060 of 8192 tokens with audio; 120 s costs ~12120 and
            # would truncate every window. Refuse at boot rather than emit an
            # INVALID_ARGUMENT storm mid-backfill.
            raise ValueError(
                "GEMINI_AUDIO_TRACK_EXTRACTION requires RAG_VIDEO_SEGMENT_S <= 60 "
                "(the shared 8192-token budget cannot hold a longer clip plus audio)"
            )
        if self.gemini_embed_task_conditioning != "off" and not media_lit:
            raise ValueError(
                "GEMINI_EMBED_TASK_CONDITIONING requires media RAG ingest or "
                "retrieval to be enabled"
            )
        if self.rag_video_caption_frames > 1 and not self.feature_media_rag_ingest:
            raise ValueError("RAG_VIDEO_CAPTION_FRAMES > 1 requires FEATURE_MEDIA_RAG_INGEST")
        return self

    # -------------------------------------------------------------------------
    # Conversation Persistence Configuration
    # -------------------------------------------------------------------------
    # The quarantined conversation-persistence/search plane and its settings
    # (CONVERSATION_PERSISTENCE_ENABLED, CONVERSATION_SEARCH_ENABLED,
    # CONVERSATION_SYNC_BATCH_SIZE, AZURE_SEARCH_INDEX_NAME) were deleted in
    # S15. Durable history lives in the aichat ledger; S20's thread search is
    # ledger-backed and must never resurrect the Azure conversation index.


class AzureOpenAISettings(BaseSettings):
    """Azure OpenAI Configuration."""

    model_config = SettingsConfigDict(
        env_prefix="AZURE_OPENAI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    endpoint: str = Field(description="Azure OpenAI endpoint URL")
    api_key: SecretStr = Field(description="Azure OpenAI API key")
    deployment: str = Field(default="gpt-4o", description="Primary model deployment name")
    fast_deployment: str = Field(default="gpt-4o-mini", description="Fast model for T1 routing")
    embedding_deployment: str = Field(
        # Kept in lockstep with Settings.azure_openai_embedding_deployment; the
        # live controlled-document index stores 3072-dim 3-large vectors.
        default="text-embedding-3-large",
        description="Embedding model deployment",
    )
    api_version: str = Field(default="2024-10-21", description="API version")


class AzureDocIntelligenceSettings(BaseSettings):
    """Azure Document Intelligence Configuration."""

    model_config = SettingsConfigDict(
        env_prefix="AZURE_DOC_INTELLIGENCE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    endpoint: str = Field(description="Document Intelligence endpoint URL")
    key: SecretStr = Field(description="Document Intelligence API key")


class InvenTreeSettings(BaseSettings):
    """InvenTree Configuration."""

    model_config = SettingsConfigDict(
        env_prefix="INVENTREE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    url: str = Field(default="http://localhost:8000/api/", description="InvenTree API URL")
    token: SecretStr = Field(description="InvenTree API token")
    timeout: int = Field(default=30, description="Request timeout in seconds")
    # Short-TTL cache for GET reads (seconds). 0 disables (default) -- caching
    # trades freshness for latency, so it is opt-in on this accuracy-sensitive
    # read path. Any write invalidates the cache.
    read_cache_ttl_s: float = Field(
        default=0.0, ge=0.0, le=300.0, description="GET read cache TTL seconds (0=off)"
    )


class GmailSettings(BaseSettings):
    """Gmail Integration Configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    email: str = Field(default="parts@equa.work", alias="GMAIL_EMAIL")
    service_account_path: Path = Field(
        default=Path("./secrets/google-service-account.json"),
        alias="GOOGLE_SERVICE_ACCOUNT_PATH",
    )
    # Raw JSON content of the service account key — preferred in containers
    # where mounting a file is impractical.  When set, the file path is ignored.
    service_account_json: str | None = Field(
        default=None,
        alias="GOOGLE_SERVICE_ACCOUNT_JSON",
    )
    # Stored as a raw string so pydantic-settings doesn't try JSON-decoding
    # a comma-separated env var as a list field.  Access via the ``scopes``
    # property which splits it into ``list[str]``.
    scopes_raw: str = Field(
        default="https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/gmail.modify",
        alias="GMAIL_SCOPES",
    )

    @property
    def scopes(self) -> list[str]:
        """Return Gmail scopes as a list, parsed from the comma-separated env var."""
        raw = self.scopes_raw
        if isinstance(raw, list):
            return raw
        return [s.strip() for s in raw.split(",") if s.strip()]


class DevUISettings(BaseSettings):
    """MAF DevUI Configuration."""

    model_config = SettingsConfigDict(
        env_prefix="DEVUI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enabled: bool = True
    port: int = 3000


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()


@lru_cache
def get_azure_openai_settings() -> AzureOpenAISettings:
    """Get cached Azure OpenAI settings."""
    # Required fields are supplied via environment variables at runtime;
    # pydantic-settings raises a ValidationError if they are missing.
    return AzureOpenAISettings()


@lru_cache
def get_azure_doc_intelligence_settings() -> AzureDocIntelligenceSettings:
    """Get cached Document Intelligence settings."""
    # Required fields are supplied via environment variables at runtime;
    # pydantic-settings raises a ValidationError if they are missing.
    return AzureDocIntelligenceSettings()


@lru_cache
def get_inventree_settings() -> InvenTreeSettings:
    """Get cached InvenTree settings."""
    # Required fields are supplied via environment variables at runtime;
    # pydantic-settings raises a ValidationError if they are missing.
    return InvenTreeSettings()


@lru_cache
def get_gmail_settings() -> GmailSettings:
    """Get cached Gmail settings."""
    return GmailSettings()


@lru_cache
def get_devui_settings() -> DevUISettings:
    """Get cached DevUI settings."""
    return DevUISettings()


# Default settings instance
settings = get_settings()


# --------------------------------------------------------------------------- #
# S44/S15: the ONE redaction authority for settings dumps. Used by the
# staff-gated /config/effective endpoint and the evaluation_dossier command.
# --------------------------------------------------------------------------- #
CONFIG_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")

#: Credential SHAPES inside string values: a SAS/API signature in a query
#: string or connection string, or userinfo credentials embedded in a URL.
#: Name-based redaction alone is one operator habit away from leaking — an
#: endpoint pasted WITH its ?sig=... survives a name check.
CONFIG_SECRET_VALUE = _re.compile(
    r"(?i)(?:sig|sharedaccesskey|accountkey|apikey|api-key|password|secret|token)="
    r"|://[^/@\s]+:[^/@\s]+@"
)


def redact_config(value, name: str = ""):
    """Redact secret-shaped values from a settings dump (S44).

    Name markers redact only STRING values — feature flags and budget
    numbers are what the dump exists to expose, not secrets. Every string
    additionally gets a value-shape scan so a credential pasted into a
    non-secret-named field never ships.
    """
    if isinstance(value, dict):
        return {key: redact_config(item, key) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_config(item, name) for item in value]
    # pydantic SecretStr dumps as the masked literal already, but be safe.
    if value.__class__.__name__ == "SecretStr":
        return "***"
    if isinstance(value, str) and value:
        upper = name.upper()
        if any(marker in upper for marker in CONFIG_SECRET_MARKERS):
            return "***"
        if CONFIG_SECRET_VALUE.search(value):
            return "***"
    return value
