"""统一检索存储模型的结构与不变量测试。"""

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from apps.retrieval.models.orm import (
    RetrievalEmbeddingModel,
    RetrievalIndexGenerationModel,
    RetrievalIndexJobModel,
    RetrievalQueryTraceModel,
    RetrievalResourceModel,
    RetrievalSourceModel,
    RetrievalUnitModel,
)


def _constraint_columns(table, constraint_type):
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, constraint_type)
    }


def test_retrieval_storage_defines_all_logical_tables():
    assert {
        RetrievalSourceModel.__table__.name,
        RetrievalResourceModel.__table__.name,
        RetrievalUnitModel.__table__.name,
        RetrievalEmbeddingModel.__table__.name,
        RetrievalIndexGenerationModel.__table__.name,
        RetrievalIndexJobModel.__table__.name,
        RetrievalQueryTraceModel.__table__.name,
    } == {
        "retrieval_source",
        "retrieval_resource",
        "retrieval_unit",
        "retrieval_embedding",
        "retrieval_index_generation",
        "retrieval_index_job",
        "retrieval_query_trace",
    }


def test_scope_and_acl_fields_are_indexable_columns_not_only_json_metadata():
    resource_columns = RetrievalResourceModel.__table__.c

    assert {
        "tenant_id",
        "namespace",
        "resource_type",
        "source_id",
        "dataset_id",
        "knowledge_base_id",
        "visibility",
        "permission_version",
        "status",
    }.issubset(resource_columns.keys())
    assert {
        "idx_retrieval_resource_dataset",
        "idx_retrieval_resource_knowledge",
    }.issubset({index.name for index in RetrievalResourceModel.__table__.indexes})


def test_composite_foreign_keys_carry_tenant_and_generation_boundaries():
    assert ("tenant_id", "source_id") in _constraint_columns(
        RetrievalResourceModel.__table__,
        ForeignKeyConstraint,
    )
    assert ("tenant_id", "resource_id") in _constraint_columns(
        RetrievalUnitModel.__table__,
        ForeignKeyConstraint,
    )
    assert ("tenant_id", "unit_id", "index_generation") in _constraint_columns(
        RetrievalEmbeddingModel.__table__,
        ForeignKeyConstraint,
    )
    assert ("tenant_id", "source_id", "resource_id") in _constraint_columns(
        RetrievalIndexJobModel.__table__,
        ForeignKeyConstraint,
    )
    assert ("tenant_id", "source_id", "target_generation") in _constraint_columns(
        RetrievalIndexJobModel.__table__,
        ForeignKeyConstraint,
    )


def test_generation_uniqueness_supports_replacement_builds_without_duplicate_active_rows():
    assert ("resource_id", "unit_key", "index_generation") in _constraint_columns(
        RetrievalUnitModel.__table__,
        UniqueConstraint,
    )
    assert ("unit_id", "embedding_profile", "index_generation") in _constraint_columns(
        RetrievalEmbeddingModel.__table__,
        UniqueConstraint,
    )

    unit_active = next(
        index for index in RetrievalUnitModel.__table__.indexes if index.name == "ux_retrieval_unit_active"
    )
    embedding_active = next(
        index
        for index in RetrievalEmbeddingModel.__table__.indexes
        if index.name == "ux_retrieval_embedding_active"
    )
    assert unit_active.unique is True
    assert str(unit_active.dialect_options["postgresql"]["where"]) == "status = 'active'"
    assert embedding_active.unique is True
    assert str(embedding_active.dialect_options["postgresql"]["where"]) == "status = 'active'"


def test_embedding_profile_is_physically_fixed_to_1024_dimensions():
    embedding_table = RetrievalEmbeddingModel.__table__
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in embedding_table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert embedding_table.c.embedding.type.dim == 1024
    assert checks["ck_retrieval_embedding_dimension"] == "dimension = 1024"
    assert "embedding IS NOT NULL" in checks["ck_retrieval_embedding_active_vector"]


def test_chinese_lexical_channel_has_partial_trigram_index():
    trigram_index = next(
        index
        for index in RetrievalUnitModel.__table__.indexes
        if index.name == "idx_retrieval_unit_trgm"
    )
    assert trigram_index.dialect_options["postgresql"]["using"] == "gin"
    assert str(trigram_index.dialect_options["postgresql"]["where"]) == "status = 'active'"
    assert "gin_trgm_ops" in str(next(iter(trigram_index.expressions)))
    resource_index = next(
        index
        for index in RetrievalResourceModel.__table__.indexes
        if index.name == "idx_retrieval_resource_title_trgm"
    )
    assert resource_index.dialect_options["postgresql"]["using"] == "gin"
    assert "gin_trgm_ops" in str(next(iter(resource_index.expressions)))


def test_generation_snapshots_profile_and_allows_only_one_active_row_per_source():
    columns = RetrievalIndexGenerationModel.__table__.c
    assert {
        "tenant_id",
        "source_id",
        "generation",
        "source_version",
        "previous_generation",
        "embedding_profile",
        "embedding_provider",
        "embedding_model",
        "embedding_dimension",
        "status",
    }.issubset(columns.keys())
    active_index = next(
        index
        for index in RetrievalIndexGenerationModel.__table__.indexes
        if index.name == "ux_retrieval_index_generation_active"
    )
    assert active_index.unique is True
    assert str(active_index.dialect_options["postgresql"]["where"]) == "status = 'active'"
