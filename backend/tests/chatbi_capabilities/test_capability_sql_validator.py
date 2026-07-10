"""能力层 SQL 校验器契约测试（平移自 v1，行为不得回归）。"""

from apps.chatbi_capabilities.sql.validator import SqlValidateTool


def test_rejects_empty_sql():
    result = SqlValidateTool().run({"sql": "  "})
    assert not result.success
    assert result.error_code == "empty_sql"


def test_rejects_multi_statement():
    result = SqlValidateTool().run({"sql": "select 1; select 2"})
    assert not result.success
    assert result.error_code == "multi_statement"


def test_rejects_non_select():
    result = SqlValidateTool().run({"sql": "update t set a = 1"})
    assert not result.success
    assert result.error_code == "unsafe_statement"


def test_rejects_unsafe_keyword_in_select():
    result = SqlValidateTool().run({"sql": "select * from t where x = (drop table u)"})
    assert not result.success
    assert result.error_code == "unsafe_statement"


def test_rejects_table_outside_allowlist():
    result = SqlValidateTool().run({"sql": "select * from secret_t", "allowed_tables": ["public_t"]})
    assert not result.success
    assert result.error_code == "unknown_table"


def test_appends_limit_when_missing():
    result = SqlValidateTool(default_limit=50).run({"sql": "select * from public_t", "allowed_tables": ["public_t"]})
    assert result.success
    assert result.payload["sql"].lower().endswith("limit 50")
    assert result.payload["tables"] == ["public_t"]


def test_keeps_existing_limit():
    result = SqlValidateTool().run({"sql": "select * from public_t limit 5", "allowed_tables": ["public_t"]})
    assert result.success
    assert result.payload["sql"].lower().endswith("limit 5")


def test_allows_with_cte():
    result = SqlValidateTool().run({"sql": "with c as (select 1) select * from c"})
    assert result.success
