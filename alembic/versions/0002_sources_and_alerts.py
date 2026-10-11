"""proof sources and alert subscriptions

Adds details about each suspension's primary source (kind, quoted text, when
it was posted), a table of extra corroborating sources, and Discord alert
subscriptions.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE_KINDS = ("facebook_post", "x_post", "news_article", "official_website", "other")


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=32)


def upgrade() -> None:
    with op.batch_alter_table("suspensions") as batch:
        batch.add_column(sa.Column("source_kind", _enum("sourcekind", *SOURCE_KINDS), nullable=True))
        batch.add_column(sa.Column("source_excerpt", sa.Text(), nullable=True))
        batch.add_column(sa.Column("source_posted_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "suspension_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("suspension_id", sa.Integer(), nullable=False),
        sa.Column("kind", _enum("sourcekind", *SOURCE_KINDS), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["suspension_id"], ["suspensions.id"],
            name=op.f("fk_suspension_sources_suspension_id_suspensions"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suspension_sources")),
    )
    op.create_index(op.f("ix_suspension_sources_suspension_id"), "suspension_sources", ["suspension_id"])

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("channel", _enum("channel", "discord_webhook"), nullable=False),
        sa.Column("target", sa.String(length=500), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("manage_token", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["location_id"], ["locations.id"],
            name=op.f("fk_subscriptions_location_id_locations"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
        sa.UniqueConstraint("manage_token", name=op.f("uq_subscriptions_manage_token")),
    )
    op.create_index(op.f("ix_subscriptions_location_id"), "subscriptions", ["location_id"])
    op.create_index(op.f("ix_subscriptions_active"), "subscriptions", ["active"])


def downgrade() -> None:
    op.drop_table("subscriptions")
    op.drop_table("suspension_sources")
    with op.batch_alter_table("suspensions") as batch:
        batch.drop_column("source_posted_at")
        batch.drop_column("source_excerpt")
        batch.drop_column("source_kind")
