"""067_semantic_metric_dimension

Revision ID: 067_semantic_metric_dimension
Revises: 8adc3a4919be
Create Date: 2026-05-25 00:00:00.000000

"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "067_semantic_metric_dimension"
down_revision = "8adc3a4919be"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'semantic_metric',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('oid', sa.BigInteger(), nullable=False),
        sa.Column('datasource_id', sa.BigInteger(), nullable=False),
        sa.Column('table_id', sa.BigInteger(), nullable=True),
        sa.Column('field_id', sa.BigInteger(), nullable=True),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column('display_name', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column('aliases', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('define_type', sqlmodel.sql.sqltypes.AutoString(length=32), server_default='MEASURE', nullable=False),
        sa.Column('expr', sa.Text(), nullable=False),
        sa.Column('default_agg', sqlmodel.sql.sqltypes.AutoString(length=32), server_default='SUM', nullable=False),
        sa.Column('filter_sql', sa.Text(), nullable=True),
        sa.Column('data_type', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column('data_format', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column('default_time_dimension_id', sa.BigInteger(), nullable=True),
        sa.Column('related_dimension_ids', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('owner_id', sa.BigInteger(), nullable=True),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=32), server_default='CANDIDATE', nullable=False),
        sa.Column('origin', sqlmodel.sql.sqltypes.AutoString(length=32), server_default='FIELD_INIT', nullable=False),
        sa.Column('embedding', VECTOR(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        sa.Column('updated_by', sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ux_semantic_metric_name', 'semantic_metric', ['oid', 'datasource_id', 'name'], unique=True)
    op.create_index('idx_semantic_metric_ds_status', 'semantic_metric', ['oid', 'datasource_id', 'status'], unique=False)
    op.create_index('idx_semantic_metric_source', 'semantic_metric', ['table_id', 'field_id'], unique=False)
    op.create_index('idx_semantic_metric_updated', 'semantic_metric', [sa.text('updated_at DESC')], unique=False)

    op.create_table(
        'semantic_dimension',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('oid', sa.BigInteger(), nullable=False),
        sa.Column('datasource_id', sa.BigInteger(), nullable=False),
        sa.Column('table_id', sa.BigInteger(), nullable=True),
        sa.Column('field_id', sa.BigInteger(), nullable=True),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column('display_name', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column('aliases', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('expr', sa.Text(), nullable=False),
        sa.Column('dimension_type', sqlmodel.sql.sqltypes.AutoString(length=32), server_default='CATEGORY', nullable=False),
        sa.Column('semantic_type', sqlmodel.sql.sqltypes.AutoString(length=32), server_default='UNKNOWN', nullable=False),
        sa.Column('data_type', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column('time_granularities', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('default_values', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('owner_id', sa.BigInteger(), nullable=True),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=32), server_default='CANDIDATE', nullable=False),
        sa.Column('origin', sqlmodel.sql.sqltypes.AutoString(length=32), server_default='FIELD_INIT', nullable=False),
        sa.Column('embedding', VECTOR(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        sa.Column('updated_by', sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ux_semantic_dimension_name', 'semantic_dimension', ['oid', 'datasource_id', 'name'], unique=True)
    op.create_index('idx_semantic_dimension_ds_status', 'semantic_dimension', ['oid', 'datasource_id', 'status'], unique=False)
    op.create_index('idx_semantic_dimension_source', 'semantic_dimension', ['table_id', 'field_id'], unique=False)
    op.create_index('idx_semantic_dimension_type', 'semantic_dimension', ['dimension_type', 'semantic_type'], unique=False)

    op.create_table(
        'semantic_dimension_value',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('dimension_id', sa.BigInteger(), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('display_value', sa.Text(), nullable=True),
        sa.Column('aliases', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('embedding', VECTOR(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ux_semantic_dimension_value', 'semantic_dimension_value', ['dimension_id', 'value'], unique=True)
    op.create_index('idx_semantic_dimension_value_enabled', 'semantic_dimension_value', ['dimension_id', 'enabled'], unique=False)

    op.create_table(
        'semantic_asset_audit',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('asset_type', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column('asset_id', sa.BigInteger(), nullable=False),
        sa.Column('action', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_semantic_asset_audit_asset', 'semantic_asset_audit', ['asset_type', 'asset_id', sa.text('created_at DESC')], unique=False)

    op.create_table(
        'chat_record_semantic_asset',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('record_id', sa.BigInteger(), nullable=False),
        sa.Column('asset_type', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column('asset_id', sa.BigInteger(), nullable=False),
        sa.Column('role', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column('match_word', sa.Text(), nullable=True),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('snapshot', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ux_chat_record_semantic_asset', 'chat_record_semantic_asset', ['record_id', 'asset_type', 'asset_id', 'role'], unique=True)
    op.create_index('idx_chat_record_semantic_asset_record', 'chat_record_semantic_asset', ['record_id'], unique=False)


def downgrade():
    op.drop_index('idx_chat_record_semantic_asset_record', table_name='chat_record_semantic_asset')
    op.drop_index('ux_chat_record_semantic_asset', table_name='chat_record_semantic_asset')
    op.drop_table('chat_record_semantic_asset')

    op.drop_index('idx_semantic_asset_audit_asset', table_name='semantic_asset_audit')
    op.drop_table('semantic_asset_audit')

    op.drop_index('idx_semantic_dimension_value_enabled', table_name='semantic_dimension_value')
    op.drop_index('ux_semantic_dimension_value', table_name='semantic_dimension_value')
    op.drop_table('semantic_dimension_value')

    op.drop_index('idx_semantic_dimension_type', table_name='semantic_dimension')
    op.drop_index('idx_semantic_dimension_source', table_name='semantic_dimension')
    op.drop_index('idx_semantic_dimension_ds_status', table_name='semantic_dimension')
    op.drop_index('ux_semantic_dimension_name', table_name='semantic_dimension')
    op.drop_table('semantic_dimension')

    op.drop_index('idx_semantic_metric_updated', table_name='semantic_metric')
    op.drop_index('idx_semantic_metric_source', table_name='semantic_metric')
    op.drop_index('idx_semantic_metric_ds_status', table_name='semantic_metric')
    op.drop_index('ux_semantic_metric_name', table_name='semantic_metric')
    op.drop_table('semantic_metric')
