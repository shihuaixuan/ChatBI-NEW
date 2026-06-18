"""074_headless_asset_governance_version

Revision ID: 074_headless_asset_gov
Revises: 073_headless_asset_doc
Create Date: 2026-06-03 00:00:00.000000

"""

revision = "074_headless_asset_gov"
down_revision = "073_headless_asset_doc"
branch_labels = None
depends_on = None


def upgrade():
    # P2 治理字段已在 072 中随 P0 主表字段补齐，本迁移用于保持后续治理版本链路。
    pass


def downgrade():
    pass
