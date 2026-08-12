"""为 Agent Run 增加不可变时间上下文。

Revision ID: 099_agent_run_temporal_context
Revises: 098_remove_agent_step_tool_facts
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "099_agent_run_temporal_context"
down_revision = "098_remove_agent_step_tool_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chatbi_agent_run",
        sa.Column(
            "temporal_context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    # 既有 Agent Run 以创建时间作为固定基准，保证部署后仍可恢复和回放。
    op.execute(
        """
        UPDATE chatbi_agent_run
        SET temporal_context = jsonb_build_object(
            'reference_at',
            to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.US') || '+08:00',
            'timezone', 'Asia/Shanghai',
            'locale', 'zh-CN',
            'week_start', 'monday',
            'fiscal_year_start_month', 1,
            'fiscal_year_label', 'start_year',
            'business_calendar_id', NULL
        )
        WHERE temporal_context = '{}'::jsonb
        """
    )
    # Graph 的 request 本身就是不可变 Run 上下文，直接按其创建时间补齐。
    op.execute(
        """
        UPDATE workflow_run
        SET request = request || jsonb_build_object(
            'temporal_context',
            jsonb_build_object(
                'reference_at',
                to_char(created_at AT TIME ZONE 'Asia/Shanghai',
                        'YYYY-MM-DD"T"HH24:MI:SS.US') || '+08:00',
                'timezone', 'Asia/Shanghai',
                'locale', 'zh-CN',
                'week_start', 'monday',
                'fiscal_year_start_month', 1,
                'fiscal_year_label', 'start_year',
                'business_calendar_id', NULL
            )
        )
        WHERE NOT request ? 'temporal_context'
        """
    )
    # 回填完成后移除临时默认值，禁止绕过 Run 创建入口写入空上下文。
    op.alter_column(
        "chatbi_agent_run",
        "temporal_context",
        server_default=None,
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE workflow_run
        SET request = request - 'temporal_context'
        WHERE request ? 'temporal_context'
        """
    )
    op.drop_column("chatbi_agent_run", "temporal_context")
