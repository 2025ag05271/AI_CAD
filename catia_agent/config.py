from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


EnvironmentName = Literal[
    "development",
    "test",
    "production",
]

LogLevel = Literal[
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
]


class Settings(BaseSettings):
    """
    Central application configuration.

    Configuration is loaded from environment variables and an
    optional local .env file. Environment variables use the
    CATIA_AGENT_ prefix.

    Settings are cached for the lifetime of the Python process.
    Restart the application after changing environment variables
    or the .env file.
    """

    # ------------------------------------------------------------------
    # Application identity
    # ------------------------------------------------------------------

    app_name: str = Field(
        default="CATIA Agent API",
        min_length=1,
        max_length=100,
    )

    app_version: str = Field(
        default="1.0.0",
        min_length=1,
        max_length=50,
    )

    environment: EnvironmentName = "development"

    runtime_mode: Literal["worker", "legacy_api"] = "worker"

    # ------------------------------------------------------------------
    # Local FastAPI server
    # ------------------------------------------------------------------

    api_host: str = Field(
        default="127.0.0.1",
        min_length=1,
        max_length=255,
    )

    api_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
    )

    api_prefix: str = Field(
        default="/api/v1",
        min_length=1,
        max_length=50,
    )

    enable_api_docs: bool = True

    # Whole-request budget for planning only; CATIA execution happens in
    # .NET between requests and is bounded by pending_run_timeout_seconds.
    # Must exceed the worst-case sum of inner timeouts (see
    # validate_timeout_hierarchy).
    request_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        le=600,
    )

    request_body_max_bytes: int = Field(
        default=1_048_576,
        ge=1_024,
        le=10_485_760,
    )

    # ------------------------------------------------------------------
    # Request and identifier limits
    # ------------------------------------------------------------------

    max_user_message_characters: int = Field(
        default=10_000,
        ge=1,
        le=100_000,
    )

    max_session_id_characters: int = Field(
        default=128,
        ge=16,
        le=512,
    )

    max_request_id_characters: int = Field(
        default=128,
        ge=16,
        le=512,
    )

    # ------------------------------------------------------------------
    # Tool-calling model
    # ------------------------------------------------------------------

    model_temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
    )

    # ------------------------------------------------------------------
    # Azure AI Foundry provider (cloud-hosted Qwen 3.6 27B)
    #
    # azure_foundry_endpoint is the base Azure AI Foundry model
    # deployment URL, without the trailing /chat/completions path
    # segment (for example
    # https://<resource>.services.ai.azure.com/models). The bearer
    # token is stored as a SecretStr so it is never rendered by repr(),
    # str(), or accidental logging.
    # ------------------------------------------------------------------

    azure_foundry_endpoint: str | None = Field(
        default=None,
        max_length=2048,
    )

    azure_foundry_api_key: SecretStr | None = Field(
        default=None,
        max_length=4096,
    )

    azure_foundry_deployment_name: str | None = Field(
        default=None,
        max_length=200,
    )

    azure_foundry_api_version: str = Field(
        default="2024-05-01-preview",
        min_length=1,
        max_length=50,
    )

    azure_foundry_request_timeout_seconds: float = Field(
        default=45.0,
        gt=0,
        le=600,
    )

    # Sent as max_tokens on every chat-completions request. Some Azure
    # AI Foundry deployments truncate or misbehave without an explicit
    # value, so this is not left to the API's own default.
    azure_foundry_max_tokens: int = Field(
        default=2048,
        ge=16,
        le=32_768,
    )

    # Qwen's vLLM chat template exposes a "thinking" mode that emits a
    # separate, very large chain-of-thought "reasoning" field alongside
    # the tool call, burning a large share of max_tokens and adding
    # latency for no planning benefit (the system prompt already
    # forbids reasoning/free-form text). Sent as
    # chat_template_kwargs.enable_thinking; default off.
    azure_foundry_enable_thinking: bool = False

    # ------------------------------------------------------------------
    # CATIA MCP server
    # ------------------------------------------------------------------

    mcp_server_url: str = (
        "http://127.0.0.1:8001/mcp"
    )

    mcp_request_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        le=120,
    )

    mcp_tool_cache_ttl_seconds: float = Field(
        default=60.0,
        gt=0,
        le=3600,
    )

    # When false, GET /ready reuses the existing MCP tool cache (same
    # as any other consumer, subject to mcp_tool_cache_ttl_seconds)
    # instead of forcing a full MCP re-discovery round trip on every
    # readiness check.
    readiness_force_mcp_refresh: bool = False

    # Shared secret the agent sends as a bearer token and the MCP server
    # requires. Empty disables MCP authentication (local dev only).
    mcp_api_key: SecretStr | None = Field(
        default=None,
        max_length=4096,
    )

    # enforce: drop tools whose definition differs from the pinned
    # manifest; warn: log and keep them; off: no pin check.
    mcp_tool_pinning_mode: Literal["enforce", "warn", "off"] = "enforce"

    mcp_max_tool_count: int = Field(default=50, ge=1, le=500)

    mcp_max_tool_description_characters: int = Field(
        default=6_000,
        ge=100,
        le=100_000,
    )

    mcp_max_tool_schema_bytes: int = Field(
        default=16_384,
        ge=256,
        le=1_048_576,
    )

    mcp_max_tool_properties: int = Field(default=30, ge=1, le=500)

    # ------------------------------------------------------------------
    # Embedding-based ask-vs-agent intent classification
    #
    # Decides, before the first planner call of a run, whether a
    # message is a concrete CATIA action request ("agentic") or a
    # general/informational message that should be routed straight to
    # route_to_general_chat, without spending a model round trip on
    # that decision (see catia_agent/intent_classifier.py). The BGE
    # GGUF model is loaded in-process; no Ollama service is required.
    # ------------------------------------------------------------------

    enable_embedding_intent_classifier: bool = True

    intent_classifier_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60,
    )

    # After an embedding failure, skip the classifier (fall back to the
    # model's own routing) for this long instead of paying the failure
    # cost on every request.
    intent_classifier_failure_cooldown_seconds: float = Field(
        default=30.0,
        ge=0,
        le=3600,
    )

    intent_classifier_model: str = Field(
        default="qllama/bge-small-en-v1.5",
        min_length=1,
        max_length=200,
    )

    intent_classifier_gguf_path: str | None = Field(
        default=None,
        max_length=2048,
    )

    # Top-K exemplar similarities averaged per intent, to reduce
    # dependence on any single exemplar phrase.
    intent_classifier_top_k: int = Field(
        default=3,
        ge=1,
        le=20,
    )

    # Minimum agentic-intent similarity score required to skip the
    # model and route directly to a CATIA planning call.
    intent_classifier_agentic_threshold: float = Field(
        default=0.48,
        ge=0.0,
        le=1.0,
    )

    # Minimum separation the agentic score must have over the
    # general-chat score. Below this margin, the safer general-chat
    # route is preferred.
    intent_classifier_agentic_margin: float = Field(
        default=0.03,
        ge=0.0,
        le=1.0,
    )

    # ------------------------------------------------------------------
    # Agent workflow limits
    # ------------------------------------------------------------------

    max_conversation_history_messages: int = Field(
        default=20,
        ge=1,
        le=200,
    )

    # When false, every Agent run starts with no prior conversation
    # turns (pronoun/ordinal resolution against earlier turns is not
    # possible), regardless of what a previous run in the same session
    # recorded. max_conversation_history_messages still bounds how much
    # history is used when this is true.
    enable_conversation_history: bool = False

    max_actions_per_run: int = Field(
        default=20,
        ge=1,
        le=100,
    )

    # When false, a run always finalizes as "completed" after its first
    # successful action instead of looping back to the planner for
    # follow-up/goal-delta planning (see README Section 8.2). This caps
    # every run at exactly one planner call, at the cost of only ever
    # executing the first action in a compound request ("open X and
    # close X" would open X and stop).
    enable_multi_action_sequences: bool = False

    max_completed_action_result_bytes: int = Field(
        default=262_144,
        ge=1_024,
        le=5_242_880,
    )

    # How long a run may wait for the .NET executor to report a CATIA
    # action result. Must comfortably exceed the slowest CATIA operation.
    pending_run_timeout_seconds: float = Field(
        default=3600.0,
        gt=0,
        le=86_400,
    )

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    log_level: LogLevel = "INFO"

    enable_json_logs: bool = True

    include_technical_details_in_errors: bool = False

    # Logs are written to the console and, when enabled, to daily files
    # <log_directory>/<prefix>_YYYYMMDD.log (created if missing).
    enable_file_logging: bool = True

    log_directory: str = Field(
        default=r"C:\Temp\AI_Logs",
        min_length=1,
        max_length=260,
    )

    log_file_retention_days: int = Field(
        default=30,
        ge=1,
        le=3650,
    )

    # Requests slower than this are logged at WARNING as well.
    slow_request_threshold_ms: float = Field(
        default=30_000,
        gt=0,
    )

    # ------------------------------------------------------------------
    # Operational and diagnostic endpoints
    # ------------------------------------------------------------------

    enable_tool_catalog_endpoint: bool = True

    enable_tool_cache_clear_endpoint: bool = True

    # A secret can be introduced later without modifying the endpoint
    # contract. An empty value means no administrative secret is required.
    admin_api_key: str | None = Field(
        default=None,
        max_length=512,
    )

    # ------------------------------------------------------------------
    # Settings source
    # ------------------------------------------------------------------

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CATIA_AGENT_",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    # ------------------------------------------------------------------
    # Field validation
    # ------------------------------------------------------------------

    @field_validator(
        "app_name",
        "app_version",
        "api_host",
        mode="before",
    )
    @classmethod
    def strip_required_strings(
        cls,
        value: object,
    ) -> object:
        if isinstance(value, str):
            stripped_value = value.strip()

            if not stripped_value:
                raise ValueError(
                    "Configuration value must not be empty."
                )

            return stripped_value

        return value

    @field_validator(
        "mcp_server_url",
    )
    @classmethod
    def validate_http_url(
        cls,
        value: str,
    ) -> str:
        normalized_value = value.strip().rstrip("/")
        parsed_url = urlparse(normalized_value)

        if parsed_url.scheme not in {"http", "https"}:
            raise ValueError(
                "Service URL must use HTTP or HTTPS."
            )

        if not parsed_url.hostname:
            raise ValueError(
                "Service URL must include a hostname."
            )

        return normalized_value

    @field_validator("api_prefix")
    @classmethod
    def normalize_api_prefix(
        cls,
        value: str,
    ) -> str:
        normalized_value = value.strip()

        if not normalized_value.startswith("/"):
            normalized_value = f"/{normalized_value}"

        normalized_value = normalized_value.rstrip("/")

        if not normalized_value:
            raise ValueError(
                "API prefix must not be empty."
            )

        return normalized_value

    @field_validator("admin_api_key")
    @classmethod
    def normalize_admin_api_key(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        normalized_value = value.strip()
        return normalized_value or None

    @field_validator("mcp_api_key", mode="before")
    @classmethod
    def normalize_mcp_api_key(
        cls,
        value: object,
    ) -> object:
        if isinstance(value, str) and not value.strip():
            return None

        return value

    @field_validator("azure_foundry_endpoint")
    @classmethod
    def normalize_azure_foundry_endpoint(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        normalized_value = value.strip().rstrip("/")

        if not normalized_value:
            return None

        parsed_url = urlparse(normalized_value)

        if parsed_url.scheme not in {"http", "https"}:
            raise ValueError(
                "azure_foundry_endpoint must use HTTP or HTTPS."
            )

        if not parsed_url.hostname:
            raise ValueError(
                "azure_foundry_endpoint must include a hostname."
            )

        return normalized_value

    @field_validator("azure_foundry_deployment_name")
    @classmethod
    def normalize_azure_foundry_deployment_name(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        normalized_value = value.strip()
        return normalized_value or None

    @model_validator(mode="after")
    def validate_model_provider_configuration(
        self,
    ) -> Settings:
        missing_fields = [
            field_name
            for field_name, field_value in (
                (
                    "azure_foundry_endpoint",
                    self.azure_foundry_endpoint,
                ),
                (
                    "azure_foundry_api_key",
                    self.azure_foundry_api_key,
                ),
                (
                    "azure_foundry_deployment_name",
                    self.azure_foundry_deployment_name,
                ),
            )
            if field_value is None
        ]

        if missing_fields:
            raise ValueError(
                "The following Azure AI Foundry settings are not "
                "configured: " + ", ".join(missing_fields)
            )

        return self

    @model_validator(mode="after")
    def validate_timeout_hierarchy(
        self,
    ) -> Settings:
        # Worst case per request: classifier + MCP discovery + planner
        # call + one duplicate-action retry. If the outer timeout could
        # fire first, the client loses the actionId of a run that was
        # already paused for execution.
        worst_case_inner_seconds = (
            self.intent_classifier_timeout_seconds
            + self.mcp_request_timeout_seconds
            + 2 * self.azure_foundry_request_timeout_seconds
        )

        if self.request_timeout_seconds <= worst_case_inner_seconds:
            raise ValueError(
                "request_timeout_seconds "
                f"({self.request_timeout_seconds}) must exceed the "
                "worst-case sum of inner timeouts "
                f"({worst_case_inner_seconds})."
            )

        return self

    @model_validator(mode="after")
    def validate_environment_policy(
        self,
    ) -> Settings:
        if (
            self.environment == "production"
            and self.runtime_mode == "legacy_api"
        ):
            if self.enable_api_docs:
                raise ValueError(
                    "API documentation must be disabled in the "
                    "production environment unless an explicit "
                    "secured documentation policy is implemented."
                )

            if self.enable_tool_cache_clear_endpoint:
                if self.admin_api_key is None:
                    raise ValueError(
                        "An admin API key is required when the "
                        "tool-cache clear endpoint is enabled in "
                        "production."
                    )

            if self.include_technical_details_in_errors:
                raise ValueError(
                    "Technical error details must not be exposed "
                    "in the production environment."
                )

            if self.mcp_api_key is None:
                raise ValueError(
                    "mcp_api_key is required in the production "
                    "environment."
                )

            if self.mcp_tool_pinning_mode != "enforce":
                raise ValueError(
                    "mcp_tool_pinning_mode must be 'enforce' in the "
                    "production environment."
                )

        return self

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def is_test(self) -> bool:
        return self.environment == "test"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def api_base_url(self) -> str:
        return (
            f"http://{self.api_host}:{self.api_port}"
        )

    @property
    def active_model_name(self) -> str:
        return self.azure_foundry_deployment_name or "unconfigured"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Load and validate application configuration once.

    Tests may call get_settings.cache_clear() after changing
    environment variables.
    """

    return Settings()


settings = get_settings()