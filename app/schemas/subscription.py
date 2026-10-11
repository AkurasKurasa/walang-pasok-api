import datetime as dt
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.location import LocationOut

# https://discord.com/api/webhooks/<id>/<token> (also the older discordapp.com host
# and the ptb/canary clients). Anything else is rejected so the server never
# posts to arbitrary URLs.
DISCORD_WEBHOOK_RE = re.compile(
    r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api(?:/v\d+)?/webhooks/\d{5,30}/[A-Za-z0-9_\-]{20,200}$"
)


def normalize_webhook_url(value: str) -> str:
    url = value.strip()
    if not DISCORD_WEBHOOK_RE.match(url):
        raise ValueError(
            "Not a Discord webhook URL. In Discord: Server Settings > Integrations > "
            "Webhooks > New Webhook > Copy Webhook URL."
        )
    return url


class DiscordAlertCreate(BaseModel):
    webhook_url: str = Field(
        description="Discord channel webhook URL. Kept secret; never returned by the API.",
        examples=["https://discord.com/api/webhooks/123456789012345678/abc..."],
    )
    location: str | None = Field(
        default=None,
        description="Location slug to follow (a city, province or region). Omit for nationwide.",
        examples=["angeles-city"],
    )
    label: str | None = Field(default=None, max_length=120, examples=["AUF CCS Discord"])
    send_test: bool = Field(default=True, description="Post a welcome message to the channel now.")

    @field_validator("webhook_url")
    @classmethod
    def check_webhook_url(cls, value: str) -> str:
        return normalize_webhook_url(value)


class DiscordAlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    location: LocationOut | None = Field(description="`null` means nationwide.")
    label: str | None
    active: bool
    created_at: dt.datetime


class DiscordAlertCreated(DiscordAlertOut):
    manage_token: str = Field(
        description="Keep this. It is shown only once and is needed to turn the alert off."
    )
