from pathlib import Path


def test_release_checklist_documents_publish_rollback_and_gray_metrics():
    checklist = Path(__file__).resolve().parents[4] / "docs/release/01-metric-dimension-semantic-layer-release-checklist.md"

    text = checklist.read_text(encoding="utf-8")

    assert "先迁移表结构" in text
    assert "再发布管理接口" in text
    assert "开启试点工作空间问答注入" in text
    assert "先关闭 `SEMANTIC_LAYER_ENABLED`" in text
    assert "保留新表" in text
    assert "先导出新表" in text
    assert "SQL 成功率" in text
    assert "语义命中率" in text
    assert "空召回率" in text
    assert "检索耗时" in text
    assert "用户反馈" in text
    assert "关闭开关后问答链路立即回到旧逻辑" in text
    assert "新表不影响旧业务读写" in text
