"""Configuration that is intentionally separate from runtime credentials."""

from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class OracleSettings(BaseSettings):
    """Oracle-only settings.

    The runtime process must never load these values. Production uses a dedicated
    database role and network policy for this process.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ORACLE_",
        extra="ignore",
    )

    app_env: str = "development"
    database_url: SecretStr = Field(
        default=SecretStr(
            "postgresql+psycopg://trust_oracle:trust_oracle@localhost:5432/trust_runtime"
        )
    )
    operator_token: SecretStr = Field(default=SecretStr("development-oracle-token"))
    runtime_public_url: str = "http://localhost:8000"
    sandbox_base_url: str = "http://localhost:3001"
    sandbox_admin_token: SecretStr = Field(default=SecretStr("local-sandbox-admin"))

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "OracleSettings":
        if self.app_env == "production":
            if (
                self.sandbox_admin_token.get_secret_value() in {"", "local-sandbox-admin"}
                or len(self.sandbox_admin_token.get_secret_value().encode()) < 32
            ):
                raise ValueError("production oracle requires a non-default sandbox admin token")
            if (
                self.operator_token.get_secret_value() in {"", "development-oracle-token"}
                or len(self.operator_token.get_secret_value().encode()) < 32
            ):
                raise ValueError("production oracle requires a non-default operator token")
            if urlsplit(self.database_url.get_secret_value()).username != "eval_oracle":
                raise ValueError("production oracle requires the least-privilege eval_oracle role")
            if any(
                urlsplit(url).scheme != "https"
                for url in (self.runtime_public_url, self.sandbox_base_url)
            ):
                raise ValueError("production oracle endpoints must use HTTPS")
        return self


def get_settings() -> OracleSettings:
    return OracleSettings()
