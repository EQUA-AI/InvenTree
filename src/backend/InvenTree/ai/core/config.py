"""
AIMMS Configuration Module

Centralized typed configuration using pydantic-settings.
Loads from environment variables with validation.
"""

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

    # -------------------------------------------------------------------------
    # Storage Paths
    # -------------------------------------------------------------------------
    data_dir: Path = Field(default=Path("./data"))
    threads_dir: Path = Field(default=Path("./data/threads"))
    checkpoints_dir: Path = Field(default=Path("./data/checkpoints"))
    cache_dir: Path = Field(default=Path("./data/cache"))

    @field_validator("data_dir", "threads_dir", "checkpoints_dir", "cache_dir", mode="after")
    @classmethod
    def ensure_directories(cls, v: Path) -> Path:
        """Ensure storage directories exist."""
        v.mkdir(parents=True, exist_ok=True)
        return v

    # -------------------------------------------------------------------------
    # HITL Configuration
    # -------------------------------------------------------------------------
    hitl_timeout_seconds: int = Field(default=300, alias="HITL_TIMEOUT_SECONDS")
    hitl_auto_reject_on_timeout: bool = Field(default=False, alias="HITL_AUTO_REJECT_ON_TIMEOUT")

    # -------------------------------------------------------------------------
    # Feature Flags
    # -------------------------------------------------------------------------
    feature_wf1_diagnostics: bool = Field(default=True, alias="FEATURE_WF1_DIAGNOSTICS")
    feature_wf2_sequential: bool = Field(default=True, alias="FEATURE_WF2_SEQUENTIAL")
    feature_wf3_concurrent: bool = Field(default=True, alias="FEATURE_WF3_CONCURRENT")
    feature_wf4_procurement: bool = Field(default=True, alias="FEATURE_WF4_PROCUREMENT")
    feature_wf5_cpq: bool = Field(default=True, alias="FEATURE_WF5_CPQ")
    feature_wf6_incoming_docs: bool = Field(default=True, alias="FEATURE_WF6_INCOMING_DOCS")
    feature_wf8_lookup: bool = Field(default=True, alias="FEATURE_WF8_LOOKUP")
    feature_capability_broker_shadow: bool = Field(
        default=True, alias="FEATURE_CAPABILITY_BROKER_SHADOW"
    )
    feature_capability_broker_enforce: bool = Field(
        default=True, alias="FEATURE_CAPABILITY_BROKER_ENFORCE"
    )
    # Which workflows the invocation guard ENFORCES on, comma separated. Any
    # other workflow runs in shadow: the guard still evaluates and logs the
    # denial reason, but the call proceeds. The specialist rails soaked
    # shadow-first (S11) and were flipped live 2026-08-06 with zero denials
    # observed; enforcement everywhere is now the DEFAULT. The env var
    # remains an override, and the floor union in the guard keeps wf8/general
    # enforced no matter what an operator sets.
    capability_broker_enforced_workflows: str = Field(
        default="wf8,general,wf2,wf3,wf4,wf6",
        alias="CAPABILITY_BROKER_ENFORCED_WORKFLOWS",
    )
    # Capability selection v2: score the aggregation/threshold *shape* of a question
    # instead of a superlative keyword whitelist, keep the read-only SQL pack
    # attached to every read selection, and allow a second adjacent pack. Without
    # it, "how many X are over N" reaches no tool that can express the question.
    # Kill switch reverts to keyword-only selection.
    feature_capability_selection_v2: bool = Field(
        default=True, alias="FEATURE_CAPABILITY_SELECTION_V2"
    )
    # Derive selection terms from live PartCategory names so deployment-specific
    # taxonomy ("Fasteners", "O-Rings") routes to the parts pack without a
    # hand-maintained synonym list. Separate flag: this is the only selection
    # input that touches the database and cache.
    feature_category_lexicon: bool = Field(default=True, alias="FEATURE_CATEGORY_LEXICON")
    # Structured question cards (S22/S23): a turn may COMPLETE with a
    # clarification question whose 2-4 options are server-derived; the answer
    # arrives as the next turn and is validated against the persisted record.
    # The flag gates ASKING only — the answer binder always runs, so flipping
    # this off drains any in-flight pending question harmlessly. Ships dark;
    # flips on only after the client that renders the card is live.
    feature_question_cards: bool = Field(default=False, alias="FEATURE_QUESTION_CARDS")
    feature_reflection_middleware: bool = Field(default=True, alias="FEATURE_REFLECTION_MIDDLEWARE")
    feature_voice_live_diagnosis: bool = Field(default=False, alias="FEATURE_VOICE_LIVE_DIAGNOSIS")
    # Safety tightening (Tier-1): restrict voice-modality lookups to read-only tools
    # and a read-only spoken prompt. Defaults on because voice is contractually
    # read-only; set false to revert voice to the full text toolset.
    feature_voice_readonly_tools: bool = Field(default=True, alias="FEATURE_VOICE_READONLY_TOOLS")
    # Tier-1 latency: answer pattern-matched voice lookups from the deterministic
    # fast path (permission-gated) instead of the LLM tool loop. Off by default.
    feature_voice_fast_path: bool = Field(default=False, alias="FEATURE_VOICE_FAST_PATH")
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
        default=True, alias="FEATURE_VOICE_WRITE_CONFIRMATION"
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
        default=True, alias="FEATURE_TURN_USAGE_PERSISTENCE"
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
    # S26: when a medium/high-confidence diagnosis cites no history and never
    # consulted the history tools, the server retrieves history evidence and
    # runs ONE stateless full-transcript continuation. Dark by default; flip
    # dev -> experimental, and raise AZURE_LUNA_DIAGNOSIS_TIMEOUT_S to 45
    # wherever this is enabled so the continuation fits the deadline.
    feature_history_enrichment: bool = Field(default=False, alias="FEATURE_HISTORY_ENRICHMENT")
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
    feature_voice_live: bool = Field(default=False, alias="FEATURE_VOICE_LIVE")
    feature_voice_live_webrtc: bool = Field(default=False, alias="FEATURE_VOICE_LIVE_WEBRTC")
    feature_voice_live_relay: bool = Field(default=False, alias="FEATURE_VOICE_LIVE_RELAY")
    # Phase 5 (optional): move the transport to a native realtime model
    # (gpt-realtime) with native semantic VAD for snappier turn-taking. Answers
    # stay governed -- create_response:false, exact-TTS, no session tools, prompt
    # of record in the repo -- so this is a transport swap, never A4/A5. Off by
    # default; requires FEATURE_VOICE_LIVE and a gpt-realtime session model.
    feature_voice_native_sts: bool = Field(default=False, alias="FEATURE_VOICE_NATIVE_STS")
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
    # for confirmation (unknown confidence always counts as below). Served to
    # the client via the capability probe so there is one source of truth.
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


class AzureFoundryMemorySettings(BaseSettings):
    """Azure Foundry Memory Store Configuration."""

    model_config = SettingsConfigDict(
        env_prefix="AZURE_FOUNDRY_MEMORY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    endpoint: str = Field(description="Foundry Memory Store endpoint")
    key: SecretStr = Field(description="Foundry Memory Store API key")


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
def get_azure_foundry_memory_settings() -> AzureFoundryMemorySettings:
    """Get cached Foundry Memory Store settings."""
    # Required fields are supplied via environment variables at runtime;
    # pydantic-settings raises a ValidationError if they are missing.
    return AzureFoundryMemorySettings()


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
