"""评测脚本对旧转换器导入路径的兼容层。"""

from apps.retrieval.legacy_contract import legacy_semantic_result_to_bundle

__all__ = ["legacy_semantic_result_to_bundle"]
