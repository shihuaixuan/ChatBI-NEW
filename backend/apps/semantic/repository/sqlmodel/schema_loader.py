from __future__ import annotations

from sqlmodel import Session, col, select

from apps.datasource.models.datasource import CoreDatasource
from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDatasetAsset,
    SemanticDatasetModelConfig,
    SemanticDimension,
    SemanticDimensionValue,
    SemanticDomain,
    SemanticMetric,
    SemanticModel,
    SemanticModelField,
    SemanticModelMeasure,
    SemanticModelRelation,
    SemanticTerm,
)
from apps.semantic.repository.schema_repository import DatasetSchemaAssets
from apps.semantic.repository.sqlmodel.results import all_results
from apps.semantic.utils.schema_selection import (
    configured_model_ids,
    selected_model_domain_ids,
)


class SemanticSchemaLoader:
    """从持久化存储加载一次 Schema 构建所需的语义资产。"""

    def __init__(self, session: Session):
        self._session = session

    def load(self, oid: int, dataset_id: int) -> DatasetSchemaAssets:
        dataset = self._session.get(SemanticDataset, dataset_id)
        if dataset is None or dataset.oid != oid or dataset.status != 1:
            raise ValueError("SEMANTIC_DATASET_NOT_FOUND")

        domain = self._session.get(SemanticDomain, dataset.domain_id)
        dataset_model_configs = all_results(
            self._session.exec(
                select(SemanticDatasetModelConfig).where(
                    SemanticDatasetModelConfig.oid == oid,
                    SemanticDatasetModelConfig.dataset_id == dataset_id,
                    SemanticDatasetModelConfig.status == 1,
                )
            )
        )
        dataset_assets = all_results(
            self._session.exec(
                select(SemanticDatasetAsset).where(
                    SemanticDatasetAsset.oid == oid,
                    SemanticDatasetAsset.dataset_id == dataset_id,
                    SemanticDatasetAsset.status == 1,
                )
            )
        )
        model_ids_in_config = configured_model_ids(dataset, dataset_model_configs)
        if model_ids_in_config:
            models = all_results(
                self._session.exec(
                    select(SemanticModel).where(
                        SemanticModel.oid == oid,
                        col(SemanticModel.id).in_(model_ids_in_config),
                        SemanticModel.status == 1,
                    )
                )
            )
            model_order = {
                model_id: index for index, model_id in enumerate(model_ids_in_config)
            }
            models.sort(key=lambda model: model_order.get(model.id, len(model_order)))
        else:
            models = all_results(
                self._session.exec(
                    select(SemanticModel).where(
                        SemanticModel.oid == oid,
                        SemanticModel.domain_id == dataset.domain_id,
                        SemanticModel.status == 1,
                    )
                )
            )

        model_domain_ids = selected_model_domain_ids(models)
        subject_domains = self._load_subject_domains(oid, model_domain_ids, domain)
        datasource_ids = {
            model.datasource_id for model in models if model.datasource_id is not None
        }
        datasources = (
            all_results(
                self._session.exec(
                    select(CoreDatasource).where(
                        CoreDatasource.oid == oid,
                        col(CoreDatasource.id).in_(datasource_ids),
                    )
                )
            )
            if datasource_ids
            else []
        )
        model_ids = [model.id for model in models if model.id is not None]
        model_fields = (
            all_results(
                self._session.exec(
                    select(SemanticModelField).where(
                        SemanticModelField.oid == oid,
                        col(SemanticModelField.model_id).in_(model_ids),
                        SemanticModelField.status == 1,
                    )
                )
            )
            if model_ids
            else []
        )
        model_measures = (
            all_results(
                self._session.exec(
                    select(SemanticModelMeasure).where(
                        SemanticModelMeasure.oid == oid,
                        col(SemanticModelMeasure.model_id).in_(model_ids),
                        SemanticModelMeasure.status == 1,
                    )
                )
            )
            if model_ids
            else []
        )
        metrics = (
            all_results(
                self._session.exec(
                    select(SemanticMetric).where(
                        SemanticMetric.oid == oid,
                        col(SemanticMetric.model_id).in_(model_ids),
                        SemanticMetric.status == 1,
                    )
                )
            )
            if model_ids
            else []
        )
        dimensions = (
            all_results(
                self._session.exec(
                    select(SemanticDimension).where(
                        SemanticDimension.oid == oid,
                        col(SemanticDimension.model_id).in_(model_ids),
                        SemanticDimension.status == 1,
                    )
                )
            )
            if model_ids
            else []
        )
        dimension_ids = [
            dimension.id for dimension in dimensions if dimension.id is not None
        ]
        dimension_values = (
            all_results(
                self._session.exec(
                    select(SemanticDimensionValue).where(
                        SemanticDimensionValue.oid == oid,
                        col(SemanticDimensionValue.dimension_id).in_(dimension_ids),
                        SemanticDimensionValue.status == 1,
                    )
                )
            )
            if dimension_ids
            else []
        )
        domain_ids = model_domain_ids or [dataset.domain_id]
        terms = all_results(
            self._session.exec(
                select(SemanticTerm).where(
                    SemanticTerm.oid == oid,
                    col(SemanticTerm.domain_id).in_(domain_ids),
                    SemanticTerm.status == 1,
                )
            )
        )
        model_relations = (
            all_results(
                self._session.exec(
                    select(SemanticModelRelation).where(
                        SemanticModelRelation.oid == oid,
                        col(SemanticModelRelation.domain_id).in_(domain_ids),
                        SemanticModelRelation.status == 1,
                    )
                )
            )
            if model_ids
            else []
        )
        return DatasetSchemaAssets(
            dataset=dataset,
            domain=domain,
            models=models,
            metrics=metrics,
            dimensions=dimensions,
            terms=terms,
            datasources=datasources,
            model_relations=model_relations,
            model_fields=model_fields,
            model_measures=model_measures,
            dimension_values=dimension_values,
            dataset_model_configs=dataset_model_configs,
            dataset_assets=dataset_assets,
            subject_domains=subject_domains,
        )

    def _load_subject_domains(
        self,
        oid: int,
        domain_ids: list[int],
        fallback_domain: SemanticDomain | None,
    ) -> list[SemanticDomain]:
        domains: list[SemanticDomain] = []
        seen: set[int] = set()
        for domain_id in domain_ids:
            if domain_id in seen:
                continue
            seen.add(domain_id)
            domain = (
                fallback_domain
                if fallback_domain is not None and fallback_domain.id == domain_id
                else None
            )
            if domain is None:
                domain = self._session.get(SemanticDomain, domain_id)
            if domain is not None and domain.oid == oid and domain.status == 1:
                domains.append(domain)
        return domains
