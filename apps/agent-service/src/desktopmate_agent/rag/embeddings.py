from collections import Counter
from hashlib import blake2b
from math import sqrt
from typing import Any, Protocol


class EmbeddingProvider(Protocol):
    def embed_query(self, text: str) -> list[float]:
        pass

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        pass


class FakeEmbeddingProvider:
    def __init__(self, *, dimensions: int = 128) -> None:
        if dimensions < 1:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token, count in Counter(_tokenize(text)).items():
            digest = blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest, "big") % self.dimensions
            vector[index] += float(count)
        return _normalize(vector)


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None:
            if api_key is None:
                raise ValueError("api_key is required when client is not provided")
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0)
        self.model = model
        self.client = client

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(model=self.model, input=texts)
        return [list(item.embedding) for item in response.data]


def create_embedding_provider(
    *,
    provider_name: str = "fake",
    dimensions: int = 128,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str = "text-embedding-3-small",
) -> EmbeddingProvider:
    normalized = provider_name.lower()
    if normalized == "fake":
        return FakeEmbeddingProvider(dimensions=dimensions)
    if normalized == "openai":
        return OpenAICompatibleEmbeddingProvider(api_key=api_key, base_url=base_url, model=model)
    raise ValueError(f"Unsupported embedding provider: {provider_name}")


def _tokenize(text: str) -> list[str]:
    lowered = text.lower()
    tokens: list[str] = []
    current = ""
    for char in lowered:
        if char.isalnum() or "\u4e00" <= char <= "\u9fff":
            current += char
        elif current:
            tokens.extend(_expand_token(current))
            current = ""
    if current:
        tokens.extend(_expand_token(current))
    return tokens


def _expand_token(token: str) -> list[str]:
    if any("\u4e00" <= char <= "\u9fff" for char in token):
        return [token[index : index + 2] for index in range(max(len(token) - 1, 1))]
    return [token]


def _normalize(vector: list[float]) -> list[float]:
    norm = sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]
