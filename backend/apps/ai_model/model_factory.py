from abc import ABC, abstractmethod
from functools import lru_cache

from langchain.chat_models.base import BaseChatModel
from langchain_community.llms import VLLMOpenAI
from langchain_openai import AzureChatOpenAI
from pydantic import SecretStr

from apps.ai_model.composition import get_ai_model_runtime_config
from apps.ai_model.models.dto import LLMConfig
from apps.ai_model.openai.llm import BaseChatOpenAI


class BaseLLM(ABC):
    """Abstract base class for large language models"""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._llm = self._init_llm()

    @abstractmethod
    def _init_llm(self) -> BaseChatModel:
        """Initialize specific large language model instance"""
        pass

    @property
    def llm(self) -> BaseChatModel:
        """Return the langchain LLM instance"""
        return self._llm


class OpenAIvLLM(BaseLLM):
    def _init_llm(self) -> VLLMOpenAI:
        return VLLMOpenAI(
            openai_api_key=self.config.api_key or 'Empty',
            openai_api_base=self.config.api_base_url,
            model_name=self.config.model_name,
            streaming=True,
            **self.config.additional_params,
        )


class OpenAIAzureLLM(BaseLLM):
    def _init_llm(self) -> AzureChatOpenAI:
        additional_params = dict(self.config.additional_params)
        api_version = additional_params.pop("api_version", None)
        deployment_name = additional_params.pop("deployment_name", None)
        return AzureChatOpenAI(
            azure_endpoint=self.config.api_base_url,
            api_key=SecretStr(self.config.api_key or "Empty"),
            model=self.config.model_name,
            api_version=api_version,
            azure_deployment=deployment_name,
            streaming=True,
            **additional_params,
        )


class OpenAILLM(BaseLLM):
    def _init_llm(self) -> BaseChatModel:
        return BaseChatOpenAI(
            model=self.config.model_name,
            api_key=SecretStr(self.config.api_key or "Empty"),
            base_url=self.config.api_base_url,
            stream_usage=True,
            **self.config.additional_params,
        )

    def generate(self, prompt: str) -> str:
        return str(self.llm.invoke(prompt).content)


class LLMFactory:
    """Large Language Model Factory Class"""

    _llm_types: dict[str, type[BaseLLM]] = {
        "openai": OpenAILLM,
        "tongyi": OpenAILLM,
        "vllm": OpenAIvLLM,
        "azure": OpenAIAzureLLM,
    }

    @classmethod
    @lru_cache(maxsize=32)
    def create_llm(cls, config: LLMConfig) -> BaseLLM:
        llm_class = cls._llm_types.get(config.model_type)
        if not llm_class:
            raise ValueError(f"Unsupported LLM type: {config.model_type}")
        return llm_class(config)

    @classmethod
    def register_llm(cls, model_type: str, llm_class: type[BaseLLM]) -> None:
        """Register new model type"""
        cls._llm_types[model_type] = llm_class


async def get_default_config(custom_model_id: int | None = None) -> LLMConfig:
    """兼容旧调用名称，配置读取统一交给 AI Model Service。"""

    return await get_ai_model_runtime_config(custom_model_id)
