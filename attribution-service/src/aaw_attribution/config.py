from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AAW_ATTRIBUTION_", extra="ignore")

    api_token: SecretStr | None = None

    @field_validator("api_token", mode="before")
    @classmethod
    def empty_token_is_none(cls, value):
        return None if value == "" else value
