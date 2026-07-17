"""Add body/body_updated_at to engineering_items and engineering_item_comments table

Revision ID: 00021
Revises: 00020
Create Date: 2026-07-16
"""
from alembic import op
import sqlalchemy as sa

revision = '00021'
down_revision = '00020'
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)

    # Add body columns to engineering_items if not present
    existing_cols = {c["name"] for c in inspector.get_columns("engineering_items")}
    if "body" not in existing_cols:
        op.add_column("engineering_items", sa.Column("body", sa.Text(), nullable=True))
    if "body_updated_at" not in existing_cols:
        op.add_column("engineering_items", sa.Column("body_updated_at", sa.DateTime(), nullable=True))

    # Create engineering_item_comments table if not present
    if "engineering_item_comments" not in inspector.get_table_names():
        op.create_table(
            "engineering_item_comments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("item_id", sa.Integer(), sa.ForeignKey("engineering_items.id", ondelete="CASCADE"), nullable=False),
            sa.Column("github_id", sa.Integer(), nullable=False, unique=True),
            sa.Column("author", sa.String(), nullable=True),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_engineering_item_comments_item_id", "engineering_item_comments", ["item_id"])


def downgrade():
    op.drop_table("engineering_item_comments")
    op.drop_column("engineering_items", "body_updated_at")
    op.drop_column("engineering_items", "body")
