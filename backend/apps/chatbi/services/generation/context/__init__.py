"""生成上下文子包：范围、运行参数、历史、Schema 与知识上下文。"""

from apps.chatbi.services.generation.context.history import project_generation_history
from apps.chatbi.services.generation.context.knowledge import (
    GenerationContextService,
    GenerationCustomPromptProvider,
    GenerationCustomPromptService,
)
from apps.chatbi.services.generation.context.runtime_settings import (
    resolve_runtime_settings,
)
from apps.chatbi.services.generation.context.schema_context import (
    GenerationSchemaContextService,
    GenerationSchemaTableRanker,
    SchemaContextService,
)
from apps.chatbi.services.generation.context.scope import (
    ADVANCED_ASSISTANT_TYPE,
    DYNAMIC_DATASOURCE_ASSISTANT_TYPES,
    PAGE_EMBEDDED_ASSISTANT_TYPE,
    resolve_generation_scope,
)

__all__ = [
    "ADVANCED_ASSISTANT_TYPE",
    "DYNAMIC_DATASOURCE_ASSISTANT_TYPES",
    "GenerationContextService",
    "GenerationCustomPromptProvider",
    "GenerationCustomPromptService",
    "GenerationSchemaContextService",
    "GenerationSchemaTableRanker",
    "SchemaContextService",
    "PAGE_EMBEDDED_ASSISTANT_TYPE",
    "project_generation_history",
    "resolve_generation_scope",
    "resolve_runtime_settings",
]
