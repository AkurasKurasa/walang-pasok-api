"""initial schema: locations, suspensions, suspension_levels

Revision ID: 0001
Revises:
Create Date: 2026-10-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    # Stored as VARCHAR (not a native DB enum) so new values need no migration.
    return sa.Enum(*values, name=name, native_enum=False, length=32)


def upgrade() -> None:
    op.create_table(
        "locations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("psgc_code", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=140), nullable=False),
        sa.Column("type", _enum("locationtype", "region", "province", "city", "municipality"), nullable=False),
        sa.Column("region", sa.String(length=120), nullable=False),
        sa.Column("province", sa.String(length=120), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["locations.id"],
            name=op.f("fk_locations_parent_id_locations"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_locations")),
        sa.UniqueConstraint("psgc_code", name=op.f("uq_locations_psgc_code")),
        sa.UniqueConstraint("slug", name=op.f("uq_locations_slug")),
    )
    op.create_index(op.f("ix_locations_parent_id"), "locations", ["parent_id"])

    op.create_table(
        "suspensions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("school_type", _enum("schooltype", "public", "private", "both"), nullable=False),
        sa.Column("mode", _enum("mode", "all_classes", "face_to_face_only"), nullable=False),
        sa.Column("time_scope", _enum("timescope", "whole_day", "morning", "afternoon"), nullable=False),
        sa.Column("until_lifted", sa.Boolean(), nullable=False),
        sa.Column("lifted_on", sa.Date(), nullable=True),
        sa.Column("reason", _enum("reason", "weather", "holiday", "event", "other"), nullable=False),
        sa.Column("reason_detail", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=False),
        sa.Column("source_name", sa.String(length=120), nullable=False),
        sa.Column("status", _enum("status", "pending", "verified"), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["location_id"], ["locations.id"],
            name=op.f("fk_suspensions_location_id_locations"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suspensions")),
    )
    op.create_index(op.f("ix_suspensions_date"), "suspensions", ["date"])
    op.create_index(op.f("ix_suspensions_location_id"), "suspensions", ["location_id"])
    op.create_index(op.f("ix_suspensions_status"), "suspensions", ["status"])

    op.create_table(
        "suspension_levels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("suspension_id", sa.Integer(), nullable=False),
        sa.Column(
            "level",
            _enum("level", "preschool", "elementary", "junior_high", "senior_high", "college", "all"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["suspension_id"], ["suspensions.id"],
            name=op.f("fk_suspension_levels_suspension_id_suspensions"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suspension_levels")),
        sa.UniqueConstraint("suspension_id", "level", name=op.f("uq_suspension_levels_suspension_id")),
    )
    op.create_index(op.f("ix_suspension_levels_level"), "suspension_levels", ["level"])
    op.create_index(op.f("ix_suspension_levels_suspension_id"), "suspension_levels", ["suspension_id"])


def downgrade() -> None:
    op.drop_table("suspension_levels")
    op.drop_table("suspensions")
    op.drop_table("locations")
