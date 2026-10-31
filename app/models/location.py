from __future__ import annotations

from enum import StrEnum

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, enum_column


class LocationType(StrEnum):
    COUNTRY = "country"  # the whole Philippines: nationwide holidays and suspensions
    REGION = "region"
    PROVINCE = "province"
    CITY = "city"
    MUNICIPALITY = "municipality"


class Location(Base):
    """A PSGC geographic unit (region, province, city or municipality).

    `parent_id` points to the unit whose suspensions also apply here: a
    component city's parent is its province, while a highly urbanized city
    (e.g. Angeles, all NCR cities) is independent of its province, so its
    parent is the region. A province-wide suspension therefore covers
    San Fernando but not Angeles, which mirrors how LGUs actually announce.
    """

    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    psgc_code: Mapped[str] = mapped_column(String(10), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(140), unique=True)
    type: Mapped[LocationType] = mapped_column(enum_column(LocationType))
    region: Mapped[str] = mapped_column(String(120))
    # Geographic province, for display and search. NCR cities have none.
    province: Mapped[str | None] = mapped_column(String(120))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), index=True
    )

    parent: Mapped[Location | None] = relationship(remote_side=[id])

    def __repr__(self) -> str:
        return f"<Location {self.slug} ({self.psgc_code})>"
