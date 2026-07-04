import os.path
import threading

import httpx
from langchain_core.embeddings import Embeddings
from langchain_huggingface import HuggingFaceEmbeddings
from pydantic import BaseModel

from common.core.config import settings

os.environ["TOKENIZERS_PARALLELISM"] = "false"


class EmbeddingModelInfo(BaseModel):
    folder: str
    name: str
    device: str = 'cpu'


local_embedding_model = EmbeddingModelInfo(folder=settings.LOCAL_MODEL_PATH,
                                           name=os.path.join(settings.LOCAL_MODEL_PATH, 'embedding',
                                                             "shibing624_text2vec-base-chinese"))

_lock = threading.Lock()
locks = {}

_embedding_model: dict[str, Embeddings | None] = {}


class OpenAICompatibleEmbeddings(Embeddings):
    """通过 OpenAI-compatible embeddings API 获取向量。"""

    def __init__(
        self,
        api_base_url: str,
        api_key: str,
        model: str,
        timeout: float,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def embed_query(self, text: str) -> list[float]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = httpx.post(
            f"{self.api_base_url}/embeddings",
            headers=headers,
            json={"model": self.model, "input": text},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return [float(value) for value in payload["data"][0]["embedding"]]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]


class EmbeddingModelCache:

    @staticmethod
    def _new_instance(config: EmbeddingModelInfo = local_embedding_model):
        provider = str(settings.EMBEDDING_PROVIDER or "").lower()
        if provider in {"openai_compatible", "api"}:
            return OpenAICompatibleEmbeddings(
                api_base_url=settings.EMBEDDING_API_BASE_URL,
                api_key=settings.EMBEDDING_API_KEY,
                model=settings.EMBEDDING_MODEL,
                timeout=settings.EMBEDDING_API_TIMEOUT,
            )
        if provider not in {"local", "huggingface"}:
            raise ValueError(f"Unsupported embedding provider: {settings.EMBEDDING_PROVIDER}")
        return HuggingFaceEmbeddings(model_name=config.name, cache_folder=config.folder,
                                     model_kwargs={'device': config.device},
                                     encode_kwargs={'normalize_embeddings': True}
                                     )

    @staticmethod
    def _get_lock(key: str = settings.DEFAULT_EMBEDDING_MODEL):
        lock = locks.get(key)
        if lock is None:
            with _lock:
                lock = locks.get(key)
                if lock is None:
                    lock = threading.Lock()
                    locks[key] = lock

        return lock

    @staticmethod
    def get_model(key: str = settings.DEFAULT_EMBEDDING_MODEL,
                  config: EmbeddingModelInfo = local_embedding_model) -> Embeddings:
        model_instance = _embedding_model.get(key)
        if model_instance is None:
            lock = EmbeddingModelCache._get_lock(key)
            with lock:
                model_instance = _embedding_model.get(key)
                if model_instance is None:
                    model_instance = EmbeddingModelCache._new_instance(config)
                    _embedding_model[key] = model_instance

        return model_instance

    @staticmethod
    def clear() -> None:
        with _lock:
            _embedding_model.clear()
            locks.clear()
