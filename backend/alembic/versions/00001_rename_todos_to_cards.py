"""rename todos to cards

Revision ID: 00001
Revises:
Create Date: 2026-06-18 00:00:00.000000
"""
from alembic import op

revision = "00001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename the main table
    op.rename_table("todos", "cards")

    # Rename the junction table and its foreign-key column
    op.rename_table("todo_tags", "card_tags")
    with op.batch_alter_table("card_tags") as batch_op:
        batch_op.alter_column("todo_id", new_column_name="card_id")


def downgrade() -> None:
    with op.batch_alter_table("card_tags") as batch_op:
        batch_op.alter_column("card_id", new_column_name="todo_id")
    op.rename_table("card_tags", "todo_tags")
    op.rename_table("cards", "todos")
