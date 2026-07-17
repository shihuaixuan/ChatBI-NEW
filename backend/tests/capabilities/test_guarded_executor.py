"""GuardedSqlExecutor 守护链与采样/统计契约测试。"""

from apps.capabilities.schemas import ToolResult
from apps.capabilities.sql.executor import GuardedSqlExecutor, _numeric_stats


class FakeExecuteTool:
    def __init__(self, fields, data):
        self.fields = fields
        self.data = data
        self.received_sql = None

    def run(self, payload):
        self.received_sql = payload["sql"]
        return ToolResult(success=True, payload={"fields": self.fields, "data": self.data})


def _executor(fields, rows, **kwargs):
    fake = FakeExecuteTool(fields, rows)
    return GuardedSqlExecutor(session=None, execute_tool=fake, **kwargs), fake


def test_guard_chain_appends_limit_and_samples_rows():
    rows = [{"city": f"c{i}", "amount": i} for i in range(30)]
    executor, fake = _executor(["city", "amount"], rows, sample_rows=10, default_limit=100)

    result = executor.run(sql="select city, amount from sales", datasource_id=1, allowed_tables=["sales"])

    assert result.success
    assert fake.received_sql.lower().endswith("limit 100")
    assert result.payload["row_count"] == 30
    assert len(result.payload["sample_rows"]) == 10
    assert len(result.payload["full_data"]) == 30
    assert result.payload["stats_summary"]["amount"]["max"] == 29


def test_guard_chain_blocks_unsafe_sql_before_execution():
    executor, fake = _executor(["a"], [])

    result = executor.run(sql="delete from sales", datasource_id=1)

    assert not result.success
    assert result.error_code == "unsafe_statement"
    assert fake.received_sql is None  # 未到执行环节


def test_guard_chain_blocks_table_outside_allowlist():
    executor, fake = _executor(["a"], [])

    result = executor.run(sql="select * from other_t", datasource_id=1, allowed_tables=["sales"])

    assert not result.success
    assert result.error_code == "unknown_table"
    assert fake.received_sql is None


def test_numeric_stats_skips_non_numeric_and_none():
    stats = _numeric_stats(
        ["name", "amount"],
        [{"name": "a", "amount": 1}, {"name": "b", "amount": None}, {"name": "c", "amount": 3.0}],
    )
    assert "name" not in stats
    assert stats["amount"] == {"min": 1.0, "max": 3.0, "sum": 4.0, "avg": 2.0}
