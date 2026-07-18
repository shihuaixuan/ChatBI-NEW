from apps.semantic.services.term_migration import (
    ExistingSemanticTermSnapshot,
    LegacyTermMigrationContext,
    LegacyTermMigrationPlanner,
    LegacyTermSnapshot,
)


def _context(**overrides) -> LegacyTermMigrationContext:
    values = {
        "active_domains": frozenset({(1, 10), (1, 11)}),
        "dataset_domains": {(1, 20): 10, (1, 21): 10, (1, 22): 11},
        "metric_domains": {(1, 100): 10},
        "dimension_domains": {(1, 200): 10},
        "default_domains": {1: 10},
        "existing_terms": {},
    }
    values.update(overrides)
    return LegacyTermMigrationContext(**values)


def test_planner_maps_aliases_dataset_scope_and_assets():
    record = LegacyTermSnapshot(
        source_id=7,
        oid=1,
        word="人气",
        description=" 访问相关指标 ",
        other_words=("热度", "访问热度"),
        aliases=("热度", "人气"),
        dataset_ids=(20, 20, 21),
        mapped_assets=(
            {"assetType": "metric", "assetId": 100},
            {"type": "DIMENSION", "id": 200},
        ),
    )

    plan = LegacyTermMigrationPlanner().plan([record], _context())

    assert plan.can_apply is True
    assert plan.to_summary()["success_count"] == 1
    candidate = plan.candidates[0]
    assert candidate.domain_id == 10
    assert candidate.alias == ("热度", "访问热度")
    assert candidate.description == "访问相关指标"
    assert candidate.related_datasets == (20, 21)
    assert candidate.related_metrics == (100,)
    assert candidate.related_dimensions == (200,)
    assert candidate.to_payload().related_datasets == [20, 21]


def test_planner_reports_unresolved_domain_instead_of_using_silent_default():
    context = _context(default_domains={})
    record = LegacyTermSnapshot(source_id=7, oid=1, word="人气")

    plan = LegacyTermMigrationPlanner().plan([record], context)

    assert plan.can_apply is False
    assert plan.failures[0].code == "LEGACY_TERM_DOMAIN_UNRESOLVED"


def test_planner_reports_dataset_domain_conflict():
    record = LegacyTermSnapshot(
        source_id=7,
        oid=1,
        word="人气",
        dataset_ids=(20, 22),
    )

    plan = LegacyTermMigrationPlanner().plan([record], _context())

    assert plan.conflicts[0].code == "LEGACY_TERM_MULTIPLE_DOMAINS"
    assert plan.candidates == ()


def test_planner_rejects_unresolved_datasource_scope():
    record = LegacyTermSnapshot(
        source_id=7,
        oid=1,
        word="人气",
        specific_ds=True,
        datasource_ids=(30,),
    )

    plan = LegacyTermMigrationPlanner().plan([record], _context())

    assert plan.failures[0].code == "LEGACY_TERM_DATASOURCE_SCOPE_UNRESOLVED"


def test_planner_maps_datasource_scope_to_configured_datasets():
    record = LegacyTermSnapshot(
        source_id=7,
        oid=1,
        word="人气",
        specific_ds=True,
        datasource_ids=(30,),
    )
    context = _context(datasource_datasets={(1, 30): (20, 21)})

    plan = LegacyTermMigrationPlanner().plan([record], context)

    assert plan.can_apply is True
    assert plan.candidates[0].domain_id == 10
    assert plan.candidates[0].related_datasets == (20, 21)
    assert plan.to_summary()["candidates"][0]["related_datasets"] == [20, 21]


def test_planner_rejects_conflicting_explicit_and_resolved_dataset_scope():
    record = LegacyTermSnapshot(
        source_id=7,
        oid=1,
        word="人气",
        specific_ds=True,
        datasource_ids=(30,),
        dataset_ids=(20,),
    )
    context = _context(datasource_datasets={(1, 30): (20, 21)})

    plan = LegacyTermMigrationPlanner().plan([record], context)

    assert plan.can_apply is False
    assert plan.conflicts[0].code == "LEGACY_TERM_DATASET_SCOPE_MISMATCH"


def test_planner_rejects_asset_from_another_domain():
    record = LegacyTermSnapshot(
        source_id=7,
        oid=1,
        word="人气",
        mapped_assets=({"type": "METRIC", "id": 100},),
    )
    context = _context(metric_domains={(1, 100): 11})

    plan = LegacyTermMigrationPlanner().plan([record], context)

    assert plan.failures[0].code == "LEGACY_TERM_ASSET_DOMAIN_MISMATCH"


def test_planner_is_idempotent_for_identical_existing_term():
    existing = ExistingSemanticTermSnapshot(
        target_id=99,
        oid=1,
        domain_id=10,
        name="人气",
        alias=("热度",),
        related_datasets=(20,),
    )
    context = _context(existing_terms={(1, 10, "人气"): existing})
    record = LegacyTermSnapshot(
        source_id=7,
        oid=1,
        word="人气",
        other_words=("热度",),
        dataset_ids=(20,),
    )

    plan = LegacyTermMigrationPlanner().plan([record], context)

    assert plan.can_apply is True
    assert plan.candidates == ()
    assert plan.skipped[0].target_id == 99


def test_planner_reports_different_existing_term_as_conflict():
    existing = ExistingSemanticTermSnapshot(
        target_id=99,
        oid=1,
        domain_id=10,
        name="人气",
        description="不同定义",
    )
    context = _context(existing_terms={(1, 10, "人气"): existing})
    record = LegacyTermSnapshot(source_id=7, oid=1, word="人气")

    plan = LegacyTermMigrationPlanner().plan([record], context)

    assert plan.conflicts[0].code == "SEMANTIC_TERM_NAME_CONFLICT"
