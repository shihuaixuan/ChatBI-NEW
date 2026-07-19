from apps.api import api_router


def test_recommended_problem_routes_keep_legacy_paths():
    paths = {
        route.path
        for route in api_router.routes
        if route.path.startswith("/recommended_problem")
    }

    assert paths == {
        "/recommended_problem/get_datasource_recommended/{ds_id}",
        "/recommended_problem/get_datasource_recommended_base/{ds_id}",
        "/recommended_problem/save_recommended_problem",
    }


def test_sql_example_routes_keep_legacy_paths():
    paths = {
        route.path
        for route in api_router.routes
        if route.path.startswith("/system/data-training")
    }

    assert paths == {
        "/system/data-training",
        "/system/data-training/export",
        "/system/data-training/page/{current_page}/{page_size}",
        "/system/data-training/template",
        "/system/data-training/uploadExcel",
        "/system/data-training/{id}/enable/{enabled}",
    }


def test_xpack_legacy_model_path_reexports_knowledge_objects():
    from apps.data_training.models.data_training_model import (
        DataTraining,
        DataTrainingInfo,
        DataTrainingInfoResult,
    )
    from apps.knowledge.models.dto import SQLExampleInput, SQLExampleResult
    from apps.knowledge.models.orm import SQLExampleModel

    assert DataTraining is SQLExampleModel
    assert DataTrainingInfo is SQLExampleInput
    assert DataTrainingInfoResult is SQLExampleResult
