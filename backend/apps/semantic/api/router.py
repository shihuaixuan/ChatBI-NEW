from fastapi import APIRouter

from apps.semantic.api import (
    contracts,
    dataset_indexes,
    dataset_schema,
    datasets,
    datasources,
    dimensions,
    domains,
    instructions,
    metrics,
    models,
    term_excel,
    terms,
)

router = APIRouter()
router.include_router(datasources.router)
router.include_router(domains.router)
router.include_router(models.router)
router.include_router(metrics.router)
router.include_router(dimensions.router)
router.include_router(datasets.router)
router.include_router(dataset_indexes.router)
router.include_router(terms.router)
router.include_router(term_excel.router)
router.include_router(dataset_schema.router)
router.include_router(contracts.router)
router.include_router(instructions.router)
