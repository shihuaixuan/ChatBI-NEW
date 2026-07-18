import pytest
from sqlmodel import Session, select

from apps.semantic.errors import SemanticValidationError
from apps.semantic.models.dto import TermPayload
from apps.semantic.models.orm import (
    SemanticAssetAlias,
    SemanticAssetRelation,
    SemanticDataset,
    SemanticDimension,
    SemanticDomain,
    SemanticMetric,
    SemanticModel,
    SemanticTerm,
)
from apps.semantic.repository.sqlmodel.term_repository import SqlModelTermRepository
from apps.semantic.repository.term_repository import TermReferenceValidation
from apps.semantic.services.term_service import SemanticTermService
from common.core.db import engine


def test_create_term_normalizes_aliases_and_reference_ids():
    repository = _TermRepository()
    service = SemanticTermService(repository, _DomainRepository())

    term = service.create_term(
        1,
        TermPayload(
            domain_id=10,
            name=" 人气 ",
            alias=["热度", "热度", "人气", ""],
            related_datasets=[20, 20],
            related_metrics=[100, 100],
            related_dimensions=[200, 200],
        ),
    )

    assert term.name == "人气"
    assert term.alias == ["热度"]
    assert term.related_datasets == [20]
    assert term.related_metrics == [100]
    assert term.related_dimensions == [200]


@pytest.mark.parametrize(
    ("validation", "expected_code"),
    [
        (
            TermReferenceValidation(invalid_dataset_ids=(20,)),
            "SEMANTIC_TERM_DATASET_REFERENCE_INVALID",
        ),
        (
            TermReferenceValidation(invalid_metric_ids=(100,)),
            "SEMANTIC_TERM_METRIC_REFERENCE_INVALID",
        ),
        (
            TermReferenceValidation(invalid_dimension_ids=(200,)),
            "SEMANTIC_TERM_DIMENSION_REFERENCE_INVALID",
        ),
    ],
)
def test_create_term_rejects_invalid_cross_resource_references(
    validation,
    expected_code,
):
    repository = _TermRepository(validation=validation)
    service = SemanticTermService(repository, _DomainRepository())

    with pytest.raises(SemanticValidationError) as exc_info:
        service.create_term(
            1,
            TermPayload(
                domain_id=10,
                name="人气",
                related_datasets=[20],
                related_metrics=[100],
                related_dimensions=[200],
            ),
        )

    assert exc_info.value.detail == expected_code


def test_create_term_rejects_duplicate_name_in_same_domain():
    repository = _TermRepository(name_exists=True)
    service = SemanticTermService(repository, _DomainRepository())

    with pytest.raises(SemanticValidationError) as exc_info:
        service.create_term(1, TermPayload(domain_id=10, name="人气"))

    assert exc_info.value.detail == "SEMANTIC_TERM_NAME_EXISTS"


def test_disabled_term_can_be_enabled_and_deleted_in_one_batch():
    term = SemanticTerm(id=7, oid=1, domain_id=10, name="人气", status=0)
    repository = _TermRepository(terms=[term])
    service = SemanticTermService(repository, _DomainRepository())

    enabled = service.set_term_enabled(1, 7, True)
    deleted = service.delete_terms(1, [7, 7])

    assert enabled.status == 1
    assert deleted == {"deleted_ids": [7]}
    assert repository.deleted_terms == [term]


def test_sqlmodel_repository_validates_tenant_and_domain_reference_scope():
    oid = 9_920_101
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection) as session:
            domain = SemanticDomain(
                oid=oid,
                name="术语校验域",
                biz_name="term_validation_domain",
            )
            session.add(domain)
            session.flush()
            model = SemanticModel(
                oid=oid,
                domain_id=domain.id or 0,
                datasource_id=99_001,
                name="术语校验模型",
                biz_name="term_validation_model",
            )
            session.add(model)
            session.flush()
            dataset = SemanticDataset(
                oid=oid,
                domain_id=domain.id or 0,
                name="术语校验数据集",
                biz_name="term_validation_dataset",
            )
            metric = SemanticMetric(
                oid=oid,
                model_id=model.id or 0,
                name="术语校验指标",
                biz_name="term_validation_metric",
            )
            dimension = SemanticDimension(
                oid=oid,
                model_id=model.id or 0,
                name="术语校验维度",
                biz_name="term_validation_dimension",
            )
            session.add(dataset)
            session.add(metric)
            session.add(dimension)
            session.flush()

            validation = SqlModelTermRepository(session).validate_references(
                oid=oid,
                domain_id=domain.id or 0,
                dataset_ids=[dataset.id or 0, 999_001],
                metric_ids=[metric.id or 0, 999_002],
                dimension_ids=[dimension.id or 0, 999_003],
            )

            assert validation == TermReferenceValidation(
                invalid_dataset_ids=(999_001,),
                invalid_metric_ids=(999_002,),
                invalid_dimension_ids=(999_003,),
            )
        transaction.rollback()


def test_sqlmodel_repository_batch_delete_cleans_term_knowledge():
    oid = 9_920_102
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection) as session:
            domain = SemanticDomain(
                oid=oid,
                name="术语删除域",
                biz_name="term_delete_domain",
            )
            session.add(domain)
            session.flush()
            dataset = SemanticDataset(
                oid=oid,
                domain_id=domain.id or 0,
                name="术语删除数据集",
                biz_name="term_delete_dataset",
            )
            session.add(dataset)
            session.flush()
            repository = SqlModelTermRepository(session)
            term = repository.create(
                SemanticTerm(
                    oid=oid,
                    domain_id=domain.id or 0,
                    name="人气",
                    alias=["热度"],
                    related_datasets=[dataset.id or 0],
                )
            )

            assert session.exec(
                select(SemanticAssetAlias).where(
                    SemanticAssetAlias.oid == oid,
                    SemanticAssetAlias.asset_type == "TERM",
                    SemanticAssetAlias.asset_id == term.id,
                )
            ).first() is not None
            assert session.exec(
                select(SemanticAssetRelation).where(
                    SemanticAssetRelation.oid == oid,
                    SemanticAssetRelation.source_type == "TERM",
                    SemanticAssetRelation.source_id == term.id,
                )
            ).first() is not None

            repository.delete_many([term])

            assert session.get(SemanticTerm, term.id) is None
            assert session.exec(
                select(SemanticAssetAlias).where(
                    SemanticAssetAlias.oid == oid,
                    SemanticAssetAlias.asset_type == "TERM",
                    SemanticAssetAlias.asset_id == term.id,
                )
            ).first() is None
            assert session.exec(
                select(SemanticAssetRelation).where(
                    SemanticAssetRelation.oid == oid,
                    SemanticAssetRelation.source_type == "TERM",
                    SemanticAssetRelation.source_id == term.id,
                )
            ).first() is None
        transaction.rollback()


class _DomainRepository:
    def is_active(self, oid, domain_id):
        return oid == 1 and domain_id == 10


class _TermRepository:
    def __init__(
        self,
        *,
        validation: TermReferenceValidation | None = None,
        name_exists: bool = False,
        terms: list[SemanticTerm] | None = None,
    ):
        self.validation = validation or TermReferenceValidation()
        self._name_exists = name_exists
        self.terms = {term.id: term for term in terms or []}
        self.deleted_terms = []

    def validate_references(
        self,
        _oid,
        _domain_id,
        _dataset_ids,
        _metric_ids,
        _dimension_ids,
    ):
        return self.validation

    def name_exists(self, _oid, _domain_id, _name, exclude_id=None):
        return self._name_exists

    def create(self, term):
        return term

    def get(self, oid, term_id):
        term = self.terms.get(term_id)
        return term if term is not None and term.oid == oid else None

    def update(self, term):
        self.terms[term.id] = term
        return term

    def delete_many(self, terms):
        self.deleted_terms.extend(terms)
