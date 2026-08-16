"""P1-7 结构化回答组装。"""

from apps.chatbi.services.generation.answer_composer.caliber_card import (
    CaliberCard,
    build_caliber_card,
)
from apps.chatbi.services.generation.answer_composer.chart_spec import (
    ChartSpec,
    derive_chart_spec,
)
from apps.chatbi.services.generation.answer_composer.claims import (
    Claim,
    ClaimBindingError,
    validate_claim_bindings,
)
from apps.chatbi.services.generation.answer_composer.composer import (
    AnswerComposer,
    AnswerComposerInput,
    AnswerComposerResult,
)

__all__ = [
    "AnswerComposer",
    "AnswerComposerInput",
    "AnswerComposerResult",
    "CaliberCard",
    "Claim",
    "ClaimBindingError",
    "ChartSpec",
    "build_caliber_card",
    "derive_chart_spec",
    "validate_claim_bindings",
]
