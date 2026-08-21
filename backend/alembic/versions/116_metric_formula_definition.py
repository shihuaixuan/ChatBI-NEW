"""新增结构化指标公式定义。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "116_metric_formula_definition"
down_revision = "115_semantic_analysis_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "headless_metric",
        sa.Column(
            "formula_definition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("headless_metric", "formula_definition")
