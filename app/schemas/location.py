from pydantic import BaseModel, ConfigDict, Field

from app.models import LocationType


class LocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    psgc_code: str = Field(examples=["0330100000"])
    name: str = Field(examples=["Angeles City"])
    slug: str = Field(examples=["angeles-city"])
    type: LocationType
    region: str = Field(examples=["Central Luzon (Region III)"])
    province: str | None = Field(default=None, examples=["Pampanga"])
