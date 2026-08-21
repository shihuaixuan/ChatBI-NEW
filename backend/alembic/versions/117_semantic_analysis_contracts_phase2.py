"""补齐第二阶段语义分析契约字段。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "117_semantic_contract_phase2"
down_revision = "116_metric_formula_definition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "headless_metric",
        sa.Column(
            "comparison_grains",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "headless_metric",
        sa.Column(
            "time_alignment_policy",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'NONE'"),
        ),
    )
    op.add_column(
        "headless_metric_dimension_capability",
        sa.Column(
            "contribution_tolerance",
            sa.Float(),
            nullable=False,
            server_default=sa.text("1e-6"),
        ),
    )
    op.add_column(
        "headless_dataset",
        sa.Column(
            "default_timezone",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'UTC'"),
        ),
    )
    op.add_column(
        "headless_dataset",
        sa.Column(
            "calendar_type",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'NATURAL'"),
        ),
    )
    op.add_column(
        "headless_dataset",
        sa.Column(
            "week_start_day",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "headless_dataset",
        sa.Column(
            "fiscal_year_start_month",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "headless_dataset",
        sa.Column("holiday_calendar_key", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("headless_dataset", "holiday_calendar_key")
    op.drop_column("headless_dataset", "fiscal_year_start_month")
    op.drop_column("headless_dataset", "week_start_day")
    op.drop_column("headless_dataset", "calendar_type")
    op.drop_column("headless_dataset", "default_timezone")
    op.drop_column(
        "headless_metric_dimension_capability", "contribution_tolerance"
    )
    op.drop_column("headless_metric", "time_alignment_policy")
    op.drop_column("headless_metric", "comparison_grains")
