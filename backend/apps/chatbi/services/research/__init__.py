"""Research 模式的需求构建公共入口；运行期能力由编排层按明确模块引用。"""

from apps.chatbi.services.research.requirements import build_research_requirement

__all__ = ["build_research_requirement"]
