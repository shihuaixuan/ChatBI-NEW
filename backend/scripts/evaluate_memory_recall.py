"""读取用户记忆标注样本并输出离线评测报告。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from apps.memory.models.dto import MemoryEvaluationRequest
from apps.memory.services import MemoryEvaluationService


def load_samples(path: Path) -> MemoryEvaluationRequest:
    """加载并校验评测样本文件。"""

    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    return MemoryEvaluationRequest.model_validate(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="评估 ChatBI 用户记忆召回质量")
    parser.add_argument(
        "--samples",
        type=Path,
        required=True,
        help="MemoryEvaluationRequest 格式的 JSON 文件",
    )
    parser.add_argument("--output", type=Path, help="可选的报告输出路径")
    args = parser.parse_args()

    request = load_samples(args.samples)
    result = MemoryEvaluationService().evaluate(request.samples)
    content = json.dumps(
        result.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    if args.output is None:
        print(content, end="")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
