"""add comment_type/diff_path/diff_line to engineering_item_comments

Part of "CodeRabbit feedback integration" (CLAUDE_CODE_INTEGRATION.md, "What's Next"):
engineering_item_comments previously only ever held GitHub issue comments. This adds room
for PR review (inline diff) comments too -- comment_type distinguishes the two,
diff_path/diff_line are populated for pr_review_comment rows only (issue comments have no
diff position). Existing rows default to "issue_comment", which is what they all are.

Revision ID: 00039
Revises: 00038
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "00039"
down_revision = "00038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("engineering_item_comments") as batch_op:
        batch_op.add_column(sa.Column("comment_type", sa.String, nullable=False, server_default="issue_comment"))
        batch_op.add_column(sa.Column("diff_path", sa.String, nullable=True))
        batch_op.add_column(sa.Column("diff_line", sa.Integer, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("engineering_item_comments") as batch_op:
        batch_op.drop_column("diff_line")
        batch_op.drop_column("diff_path")
        batch_op.drop_column("comment_type")
