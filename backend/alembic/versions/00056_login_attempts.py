"""Add login_attempts table for per-IP login lockout

Replaces the old single global auth_failed_attempts/auth_lockout_until AppSetting
values, which meant one shared lockout counter for every caller -- anyone who found
this app's public URL could lock the real owner out just by sending 5 wrong
passwords repeatedly, with no way to tell attacker from owner. See
routers/auth.py's _client_ip for how the IP is derived behind Cloud Run's proxy.

Revision ID: 00056
Revises: 00055
Create Date: 2026-09-11
"""
from alembic import op
import sqlalchemy as sa

revision = "00056"
down_revision = "00055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_attempts",
        sa.Column("ip", sa.String(), primary_key=True),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lockout_until", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("login_attempts")
