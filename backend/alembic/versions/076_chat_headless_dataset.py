"""076_chat_headless_dataset

Revision ID: 076_chat_headless_dataset
Revises: 075_graph_workflow_runtime
Create Date: 2026-06-23 00:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "076_chat_headless_dataset"
down_revision = "075_graph_workflow_runtime"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    connection.execute(sa.text("DELETE FROM chat_log WHERE type = '0' AND pid IN (SELECT id FROM chat_record)"))
    connection.execute(sa.text("DELETE FROM chat_record"))
    connection.execute(sa.text("DELETE FROM chat"))

    op.add_column("chat", sa.Column("dataset_id", sa.BigInteger(), nullable=True))
    op.add_column("chat_record", sa.Column("dataset_id", sa.BigInteger(), nullable=True))
    op.create_index("idx_chat_dataset", "chat", ["oid", "dataset_id", sa.text("create_time DESC")])
    op.create_index("idx_chat_record_dataset", "chat_record", ["chat_id", "dataset_id"])


def downgrade():
    op.drop_index("idx_chat_record_dataset", table_name="chat_record")
    op.drop_index("idx_chat_dataset", table_name="chat")
    op.drop_column("chat_record", "dataset_id")
    op.drop_column("chat", "dataset_id")
