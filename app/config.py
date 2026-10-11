"""Application settings, loaded from environment variables (and `.env`)."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://walang_pasok:walang_pasok@localhost:5432/walang_pasok"
    admin_api_key: str = "change-me"

    # "Today" is always computed in this timezone, never the server's.
    timezone: str = "Asia/Manila"

    # Public address of the website, used in links inside Discord/Facebook posts.
    public_base_url: str = "http://localhost:8000"

    # Auto-post verified suspensions to your own Facebook Page. Leave blank to turn off.
    # Needs a Page access token with the pages_manage_posts permission (see README).
    facebook_page_id: str = ""
    facebook_page_access_token: str = ""
    facebook_graph_version: str = "v23.0"
    # Shown on the website as "Follow us on Facebook" when set.
    facebook_page_url: str = ""

    # Private Discord channel where the watcher reports new pending suspensions to review.
    admin_discord_webhook_url: str = ""

    # Timeout for each outgoing Discord/Facebook request, in seconds.
    notify_timeout: float = 10.0

    default_page_size: int = 20
    max_page_size: int = 100


    @field_validator("database_url")
    @classmethod
    def use_psycopg_driver(cls, url: str) -> str:
        """Hosts like Neon and Render give `postgresql://...` or `postgres://...` URLs;
        SQLAlchemy needs to be told to use the psycopg (v3) driver installed here."""
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url.removeprefix(prefix)
        return url

    @property
    def facebook_enabled(self) -> bool:
        return bool(self.facebook_page_id and self.facebook_page_access_token)


@lru_cache
def get_settings() -> Settings:
    return Settings()
