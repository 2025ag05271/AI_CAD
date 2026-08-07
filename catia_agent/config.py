from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
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

    request_timeout_seconds: float = Field(
        default=60.0,
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
    # Current model provider
    #
    # These settings intentionally retain Ollama for the current
    # implementation. The planner will later be isolated behind a model
    # client abstraction, allowing an Azure implementation to replace it
    # without changing the graph or API contracts.
    # ------------------------------------------------------------------

    ollama_base_url: str = (
        "http://127.0.0.1:11434"
    )

    ollama_model: str = Field(
        default="qwen3:4b",
        min_length=1,
        max_length=200,
    )

    ollama_request_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=600,
    )

    model_temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
    )

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

    # ------------------------------------------------------------------
    # Agent workflow limits
    # ------------------------------------------------------------------

    max_conversation_history_messages: int = Field(
        default=20,
        ge=1,
        le=200,
    )

    max_actions_per_run: int = Field(
        default=20,
        ge=1,
        le=100,
    )

    max_completed_action_result_bytes: int = Field(
        default=262_144,
        ge=1_024,
        le=5_242_880,
    )

    pending_run_timeout_seconds: float = Field(
        default=1800.0,
        gt=0,
        le=86_400,
    )

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    log_level: LogLevel = "INFO"

    enable_json_logs: bool = True

    include_technical_details_in_errors: bool = False

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
        "ollama_model",
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
        "ollama_base_url",
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

    @model_validator(mode="after")
    def validate_environment_policy(
        self,
    ) -> Settings:
        if self.environment == "production":
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Load and validate application configuration once.

    Tests may call get_settings.cache_clear() after changing
    environment variables.
    """

    return Settings()


settings = get_settings()