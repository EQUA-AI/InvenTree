"""
AIMMS Configuration Module

Centralized typed configuration using pydantic-settings.
Loads from environment variables with validation.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    AIMMS Backend Configuration.

    All settings are loaded from environment variables.
    Use .env file for local development.
    """

    model_config = SettingsConfigDict(
        env_prefix="AIMMS_",
        env_file=".env",
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
    host: str = "0.0.0.0"
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
    # Semantic Cache Configuration
    # -------------------------------------------------------------------------
    semantic_cache_enabled: bool = Field(default=True, alias="SEMANTIC_CACHE_ENABLED")
    semantic_cache_similarity_threshold: float = Field(
        default=0.92,
        alias="SEMANTIC_CACHE_SIMILARITY_THRESHOLD",
        ge=0.0,
        le=1.0,
    )
    semantic_cache_ttl_hours: int = Field(default=24, alias="SEMANTIC_CACHE_TTL_HOURS")

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
    feature_reflection_middleware: bool = Field(default=True, alias="FEATURE_REFLECTION_MIDDLEWARE")
    feature_voice_live_diagnosis: bool = Field(default=False, alias="FEATURE_VOICE_LIVE_DIAGNOSIS")

    # -------------------------------------------------------------------------
    # WS3 Foundry reasoning adapter
    # -------------------------------------------------------------------------
    # The owner-selected primary path references a pinned Foundry project agent.
    # Explicit aliases retain the deployment's AZURE_* setting names while the
    # rest of this settings object continues to use its AIMMS_ prefix.
    azure_voice_reasoning_invocation_mode: Literal["agent_reference", "direct_deployment"] = Field(
        default="agent_reference", alias="AZURE_VOICE_REASONING_INVOCATION_MODE"
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
        elif not (self.azure_luna_endpoint or self.azure_openai_endpoint):
            # The alternate deployment is only required when explicitly
            # selected. Diagnosis remains feature-gated off by default.
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
    # WS6 pilot cohort: explicit server-side user ids. Empty means no cohort,
    # so enabling the feature flags alone can never expose voice to anyone.
    voice_pilot_user_ids: list[int] = Field(
        default_factory=list, alias="AIMMS_VOICE_PILOT_USER_IDS"
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
        default="text-embedding-ada-002", alias="AZURE_OPENAI_EMBEDDING_DEPLOYMENT"
    )
    azure_openai_api_version: str = Field(default="2024-10-21", alias="AZURE_OPENAI_API_VERSION")

    # -------------------------------------------------------------------------
    # Azure AI Search Configuration
    # -------------------------------------------------------------------------
    azure_search_endpoint: str = Field(default="", alias="AZURE_SEARCH_ENDPOINT")
    azure_search_api_key: str = Field(default="", alias="AZURE_SEARCH_API_KEY")
    azure_search_index_name: str = Field(
        default="inventree-ai-conversations", alias="AZURE_SEARCH_INDEX_NAME"
    )
    azure_search_documents_index: str = Field(default="", alias="AZURE_SEARCH_DOCUMENTS_INDEX")

    # -------------------------------------------------------------------------
    # Conversation Persistence Configuration
    # -------------------------------------------------------------------------
    conversation_persistence_enabled: bool = Field(
        default=True, alias="CONVERSATION_PERSISTENCE_ENABLED"
    )
    conversation_search_enabled: bool = Field(default=True, alias="CONVERSATION_SEARCH_ENABLED")
    conversation_sync_batch_size: int = Field(default=50, alias="CONVERSATION_SYNC_BATCH_SIZE")


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
        default="text-embedding-ada-002", description="Embedding model deployment"
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
    return AzureOpenAISettings()


@lru_cache
def get_azure_doc_intelligence_settings() -> AzureDocIntelligenceSettings:
    """Get cached Document Intelligence settings."""
    return AzureDocIntelligenceSettings()


@lru_cache
def get_azure_foundry_memory_settings() -> AzureFoundryMemorySettings:
    """Get cached Foundry Memory Store settings."""
    return AzureFoundryMemorySettings()


@lru_cache
def get_inventree_settings() -> InvenTreeSettings:
    """Get cached InvenTree settings."""
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
