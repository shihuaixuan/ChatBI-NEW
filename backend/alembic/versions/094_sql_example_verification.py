"""SQL 示例独立验证状态

Revision ID: 094_sql_example_verification
Revises: 093_access_policy_constraints
Create Date: 2026-07-19 00:00:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "094_sql_example_verification"
down_revision = "093_access_policy_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "data_training",
        sa.Column("verification_status", sa.String(length=32), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE data_training AS example
            SET verification_status = CASE
                WHEN btrim(COALESCE(example.question, '')) <> ''
                 AND btrim(COALESCE(example.description, '')) <> ''
                 AND (
                     example.datasource IS NOT NULL
                     OR example.advanced_application IS NOT NULL
                     OR example.dataset_id IS NOT NULL
                 )
                 AND (
                     example.datasource IS NULL
                     OR EXISTS (
                         SELECT 1
                         FROM core_datasource AS datasource
                         WHERE datasource.id = example.datasource
                           AND datasource.oid = example.oid
                     )
                 )
                 AND (
                     example.advanced_application IS NULL
                     OR EXISTS (
                         SELECT 1
                         FROM sys_assistant AS assistant
                         WHERE assistant.id = example.advanced_application
                           AND assistant.oid = example.oid
                           AND assistant.type = 1
                     )
                 )
                 AND (
                     example.dataset_id IS NULL
                     OR EXISTS (
                         SELECT 1
                         FROM headless_dataset AS dataset
                         WHERE dataset.id = example.dataset_id
                           AND dataset.oid = example.oid
                           AND dataset.status = 1
                     )
                 )
                 AND jsonb_typeof(COALESCE(example.linked_assets, '[]'::jsonb))
                     = 'array'
                 AND (
                     example.dataset_id IS NOT NULL
                     OR jsonb_array_length(
                         CASE
                             WHEN jsonb_typeof(
                                 COALESCE(example.linked_assets, '[]'::jsonb)
                             ) = 'array'
                             THEN COALESCE(example.linked_assets, '[]'::jsonb)
                             ELSE '[]'::jsonb
                         END
                     ) = 0
                 )
                 AND NOT EXISTS (
                     SELECT 1
                     FROM jsonb_array_elements(
                         CASE
                             WHEN jsonb_typeof(
                                 COALESCE(example.linked_assets, '[]'::jsonb)
                             ) = 'array'
                             THEN COALESCE(example.linked_assets, '[]'::jsonb)
                             ELSE '[]'::jsonb
                         END
                     ) AS linked_asset
                     WHERE upper(
                         COALESCE(
                             linked_asset ->> 'asset_type',
                             linked_asset ->> 'assetType',
                             linked_asset ->> 'type',
                             ''
                         )
                     ) NOT IN ('METRIC', 'DIMENSION')
                        OR COALESCE(
                            linked_asset ->> 'asset_id',
                            linked_asset ->> 'assetId',
                            linked_asset ->> 'id',
                            ''
                        ) !~ '^[1-9][0-9]*$'
                        OR NOT EXISTS (
                            SELECT 1
                            FROM headless_dataset_asset AS dataset_asset
                            WHERE dataset_asset.oid = example.oid
                              AND dataset_asset.dataset_id = example.dataset_id
                              AND dataset_asset.status = 1
                              AND dataset_asset.asset_type = upper(
                                  COALESCE(
                                      linked_asset ->> 'asset_type',
                                      linked_asset ->> 'assetType',
                                      linked_asset ->> 'type',
                                      ''
                                  )
                              )
                              AND dataset_asset.asset_id = CASE
                                  WHEN COALESCE(
                                      linked_asset ->> 'asset_id',
                                      linked_asset ->> 'assetId',
                                      linked_asset ->> 'id',
                                      ''
                                  ) ~ '^[1-9][0-9]*$'
                                  THEN COALESCE(
                                      linked_asset ->> 'asset_id',
                                      linked_asset ->> 'assetId',
                                      linked_asset ->> 'id'
                                  )::bigint
                                  ELSE NULL
                              END
                        )
                 )
                THEN 'VERIFIED'
                ELSE 'UNVERIFIED'
            END
            """
        )
    )
    op.alter_column(
        "data_training",
        "verification_status",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default=sa.text("'UNVERIFIED'"),
    )
    op.create_check_constraint(
        "ck_data_training_verification_status",
        "data_training",
        "verification_status IN ('UNVERIFIED', 'VERIFIED')",
    )
    op.create_index(
        "ix_data_training_retrieval_scope",
        "data_training",
        ["oid", "enabled", "verification_status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_data_training_retrieval_scope",
        table_name="data_training",
    )
    op.drop_constraint(
        "ck_data_training_verification_status",
        "data_training",
        type_="check",
    )
    op.drop_column("data_training", "verification_status")
