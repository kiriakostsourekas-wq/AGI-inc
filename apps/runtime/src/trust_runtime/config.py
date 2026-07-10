"""Validated runtime configuration with fail-closed public deployment rules."""

from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from trust_contracts import normalize_origin


class AppEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class AgentProvider(StrEnum):
    MOCK = "mock"
    OPENAI = "openai"


class StateStoreBackend(StrEnum):
    MEMORY = "memory"
    POSTGRES = "postgres"


class ObjectStorageBackend(StrEnum):
    FILESYSTEM = "filesystem"
    S3 = "s3"


_DEVELOPMENT_SECRET_PREFIX = "development-only-trust-runtime-"  # noqa: S105 - marker, not a secret


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        extra="ignore",
        case_sensitive=False,
        validate_default=True,
    )

    app_env: AppEnvironment = AppEnvironment.DEVELOPMENT
    public_base_url: str = "http://localhost:3000"
    runtime_base_url: str = "http://localhost:8000"
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://trust:trust@localhost:5432/trust_runtime"
    )
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=5, ge=0, le=100)
    database_pool_timeout_seconds: int = Field(default=10, ge=1, le=120)
    database_statement_timeout_ms: int = Field(default=5000, ge=100, le=120_000)
    state_store_backend: StateStoreBackend = StateStoreBackend.MEMORY
    agent_provider: AgentProvider = AgentProvider.MOCK
    agent_model: str | None = None
    openai_api_key: SecretStr | None = None
    agent_temperature: str = "0.1"
    agent_max_output_tokens: int = Field(default=2000, ge=1, le=100_000)
    agent_request_timeout_seconds: int = Field(default=45, ge=1, le=300)
    model_input_cost_per_million_usd: Decimal = Field(default=Decimal("0"), ge=0)
    model_output_cost_per_million_usd: Decimal = Field(default=Decimal("0"), ge=0)
    browser_channel: str | None = None
    approval_hmac_secret: SecretStr = SecretStr(_DEVELOPMENT_SECRET_PREFIX + "approval-key")
    artifact_signing_secret: SecretStr = SecretStr(_DEVELOPMENT_SECRET_PREFIX + "artifact-key")
    artifact_storage_dir: Path = Path("artifacts/runtime")
    object_storage_backend: ObjectStorageBackend = ObjectStorageBackend.FILESYSTEM
    object_storage_path: str = "artifacts"
    object_storage_bucket: str | None = None
    object_storage_endpoint: str | None = None
    object_storage_access_key: SecretStr | None = None
    object_storage_secret_key: SecretStr | None = None
    sandbox_admin_token: SecretStr = SecretStr("local-sandbox-admin")
    sandbox_gateway_token: SecretStr = SecretStr(_DEVELOPMENT_SECRET_PREFIX + "sandbox-gateway")
    public_live_runs_enabled: bool = False
    max_public_concurrent_runs: int = Field(default=1, ge=1, le=20)
    public_runs_per_ip_per_hour: int = Field(default=1, ge=1, le=100)
    public_session_ttl_seconds: int = Field(default=3600, ge=60, le=86_400)
    public_artifact_ttl_seconds: int = Field(default=86_400, ge=300, le=604_800)
    run_max_steps: int = Field(default=30, ge=1, le=500)
    run_max_model_calls: int = Field(default=20, ge=1, le=500)
    run_max_replans: int = Field(default=4, ge=0, le=50)
    run_max_wall_seconds: int = Field(default=300, ge=1, le=3600)
    run_max_model_cost_usd: str = "0.50"
    approval_ttl_seconds: int = Field(default=180, ge=15, le=900)
    browser_allowed_origins: Annotated[tuple[str, ...], NoDecode] = (
        "http://gomail.localhost:3001",
        "http://northstar.localhost:3001",
        "http://dayplan.localhost:3001",
    )
    service_allowed_hosts: Annotated[tuple[str, ...], NoDecode] = ("api.openai.com",)
    evaluation_operator_token: SecretStr = SecretStr(
        _DEVELOPMENT_SECRET_PREFIX + "evaluation-operator"
    )
    evaluation_max_total_cost_usd: Decimal = Field(default=Decimal("25.00"), gt=0)
    git_commit_sha: str | None = None
    browser_version: str | None = None
    model_price_table_version: str = "development-v1"
    fault_manifest_version: str = "1.0.0"
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "trust-runtime"
    log_level: str = "INFO"

    @field_validator("browser_allowed_origins", "service_allowed_hosts", mode="before")
    @classmethod
    def parse_csv_tuple(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @field_validator("browser_allowed_origins")
    @classmethod
    def validate_browser_origins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("browser origin allowlist must not be empty")
        normalized = tuple(normalize_origin(value) for value in values)
        if len(set(normalized)) != len(normalized):
            raise ValueError("browser origin allowlist must not contain duplicates")
        return normalized

    @field_validator("service_allowed_hosts")
    @classmethod
    def validate_service_hosts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any("/" in value or ":" in value or not value for value in values):
            raise ValueError("service allowlist entries must be hostnames without scheme or port")
        normalized = tuple(value.lower() for value in values)
        if len(set(normalized)) != len(normalized):
            raise ValueError("service allowlist must not contain duplicates")
        return normalized

    @field_validator("agent_temperature")
    @classmethod
    def validate_temperature(cls, value: str) -> str:
        try:
            temperature = Decimal(value)
        except Exception as error:
            raise ValueError("agent temperature must be numeric") from error
        if not Decimal("0") <= temperature <= Decimal("2"):
            raise ValueError("agent temperature must be between 0 and 2")
        return format(temperature, "f")

    @model_validator(mode="after")
    def validate_live_configuration(self) -> Self:
        if self.agent_provider is AgentProvider.OPENAI:
            if not self.agent_model:
                raise ValueError("AGENT_MODEL is required for the OpenAI provider")
            if self.openai_api_key is None or not self.openai_api_key.get_secret_value():
                raise ValueError("OPENAI_API_KEY is required for the OpenAI provider")
            if (
                self.model_input_cost_per_million_usd <= 0
                or self.model_output_cost_per_million_usd <= 0
            ):
                raise ValueError("OpenAI provider requires positive pinned token prices")
        if self.app_env is AppEnvironment.PRODUCTION and self.public_live_runs_enabled:
            secret_values = (
                self.approval_hmac_secret.get_secret_value(),
                self.artifact_signing_secret.get_secret_value(),
                self.sandbox_admin_token.get_secret_value(),
                self.sandbox_gateway_token.get_secret_value(),
                self.evaluation_operator_token.get_secret_value(),
            )
            if any(
                value.startswith(_DEVELOPMENT_SECRET_PREFIX) or len(value.encode("utf-8")) < 32
                for value in secret_values
            ):
                raise ValueError(
                    "public live runs require non-default secrets of at least 32 bytes"
                )
            if not self.browser_allowed_origins or self.public_runs_per_ip_per_hour < 1:
                raise ValueError("public live runs require an origin allowlist and rate limit")
            if self.state_store_backend is not StateStoreBackend.POSTGRES:
                raise ValueError("public live runs require PostgreSQL structured state")
            if self.agent_provider is not AgentProvider.OPENAI:
                raise ValueError("production public live runs require the OpenAI provider")
            for field_name, value in (
                ("PUBLIC_BASE_URL", self.public_base_url),
                ("RUNTIME_BASE_URL", self.runtime_base_url),
            ):
                parsed = urlsplit(value)
                if parsed.scheme != "https" or parsed.hostname is None:
                    raise ValueError(f"{field_name} must be an HTTPS origin in production")
            if any(not origin.startswith("https://") for origin in self.browser_allowed_origins):
                raise ValueError("production browser origins must use HTTPS")
            browser_hosts = {urlsplit(origin).hostname for origin in self.browser_allowed_origins}
            if browser_hosts.intersection(self.service_allowed_hosts):
                raise ValueError("browser and service egress allowlists must be disjoint")
            database_user = urlsplit(self.database_url.get_secret_value()).username
            if database_user != "runtime_app":
                raise ValueError("production runtime requires the least-privilege runtime_app role")
            if self.object_storage_backend is not ObjectStorageBackend.S3:
                raise ValueError(
                    "production public live runs require S3-compatible artifact storage"
                )
            if not all(
                (
                    self.object_storage_bucket,
                    self.object_storage_endpoint,
                    self.object_storage_access_key,
                    self.object_storage_secret_key,
                )
            ):
                raise ValueError("S3 artifact storage requires bucket, endpoint, and credentials")
            assert self.object_storage_endpoint is not None
            object_endpoint = urlsplit(self.object_storage_endpoint)
            if object_endpoint.scheme != "https" or object_endpoint.hostname is None:
                raise ValueError("production object storage endpoint must use HTTPS")
            required_service_hosts = {"api.openai.com", object_endpoint.hostname}
            if self.otel_exporter_otlp_endpoint is not None:
                otel_endpoint = urlsplit(self.otel_exporter_otlp_endpoint)
                if otel_endpoint.scheme != "https" or otel_endpoint.hostname is None:
                    raise ValueError("production OTLP endpoint must use HTTPS")
                required_service_hosts.add(otel_endpoint.hostname)
            missing_service_hosts = required_service_hosts.difference(self.service_allowed_hosts)
            if missing_service_hosts:
                raise ValueError(
                    "required provider/storage/telemetry host is absent from the service egress "
                    f"allowlist: {sorted(missing_service_hosts)}"
                )
            if (
                self.git_commit_sha is None
                or len(self.git_commit_sha) != 40
                or any(character not in "0123456789abcdef" for character in self.git_commit_sha)
                or not self.browser_version
                or self.model_price_table_version == "development-v1"
            ):
                raise ValueError(
                    "production live runs require pinned commit, browser, and price versions"
                )
        return self

    def safe_summary(self) -> dict[str, object]:
        return {
            "app_env": self.app_env.value,
            "agent_provider": self.agent_provider.value,
            "agent_model": self.agent_model,
            "public_live_runs_enabled": self.public_live_runs_enabled,
            "state_store_backend": self.state_store_backend.value,
            "object_storage_backend": self.object_storage_backend.value,
            "browser_allowed_origins": self.browser_allowed_origins,
            "fault_manifest_version": self.fault_manifest_version,
        }
