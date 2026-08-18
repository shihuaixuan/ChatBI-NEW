"""R4 黄金题 schema 校验入口。"""

from __future__ import annotations

try:
    from scripts.validate_p1_golden_cases import (
        CASES_PATH,
        SCHEMA_PATH,
        load_and_validate_cases,
    )
except ModuleNotFoundError:
    from validate_p1_golden_cases import (  # type: ignore[no-redef]
        CASES_PATH,
        SCHEMA_PATH,
        load_and_validate_cases,
    )


def main() -> int:
    cases = load_and_validate_cases(CASES_PATH, SCHEMA_PATH)
    print(f"validated {len(cases)} golden cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
