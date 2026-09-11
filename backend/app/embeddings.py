"""Embedding engine: dense semantic vectors plus sparse lexical weights.

The default ``hashing`` provider is deterministic, offline, and dependency
free: tokens (including camelCase / snake_case sub-tokens and character
trigrams) are hashed into a fixed-dimension L2-normalised dense vector, and
term frequencies are converted into a sparse vector suitable for Qdrant's
sparse vectors and in-memory BM25 scoring alike.

Swap in a hosted embedding model by setting ``EMBEDDING_PROVIDER=openai``
with ``OPENAI_API_KEY``. Sparse weights are always produced locally so
lexical signals keep working regardless of provider.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass, field

_TOKEN_RE = re.compile(r"[A-Za-z_$][\w$]*")
_SUB_RE = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])|\d+")

_SPARSE_SPACE = 1_000_003  # sparse bucket count (prime)
_TRIGRAM_WEIGHT = 0.15


@dataclass
class Embedding:
    """Result of embedding one piece of text."""

    dense: list[float]
    sparse_indices: list[int]
    sparse_values: list[float]
    tokens: list[str] = field(default_factory=list)


def tokenize(text: str) -> list[str]:
    """Split text into code-aware tokens.

    Produces the lowercase identifier itself plus camelCase / snake_case
    sub-tokens, so ``getUserData`` matches both ``getUserData`` and ``user``,
    ``get``, ``data``.
    """
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        lowered = raw.lower()
        tokens.append(lowered)
        if re.search(r"[A-Z_]", raw) or "$" in raw:
            for part in _SUB_RE.findall(raw):
                sub = part.lower()
                if len(sub) > 1 and sub != lowered:
                    tokens.append(sub)
    return tokens


def _hash(text: str, salt: int) -> int:
    digest = hashlib.blake2b(f"{salt}:{text}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        norm = 1.0
    return [value / norm for value in values]


class HashingEmbedder:
    """Deterministic hashing embedder that needs no network or model files."""

    name = "hashing-v1"

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> Embedding:
        tokens = tokenize(text)
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1

        dense = [0.0] * self.dimensions
        for token, tf in counts.items():
            weight = 1.0 + math.log(tf)
            for salt in (0, 1):
                bucket = _hash(token, salt) % self.dimensions
                sign = 1.0 if _hash(token, 2 + salt) % 2 == 0 else -1.0
                dense[bucket] += sign * weight
        # Character trigrams soften the collision damage of bag-of-tokens
        # hashing and make short identifiers searchable.
        lowered = text.lower()
        for position in range(len(lowered) - 2):
            gram = lowered[position : position + 3]
            if not gram.strip():
                continue
            dense[_hash("3:" + gram, 3) % self.dimensions] += _TRIGRAM_WEIGHT
        dense = _normalize(dense)

        sparse: dict[int, float] = {}
        for token, tf in counts.items():
            bucket = _hash(token, 7) % _SPARSE_SPACE
            sparse[bucket] = sparse.get(bucket, 0.0) + 1.0 + math.log(tf)
        indices = sorted(sparse)
        values = [sparse[index] for index in indices]

        return Embedding(dense=dense, sparse_indices=indices, sparse_values=values, tokens=tokens)

    def embed_dense(self, text: str) -> list[float]:
        return self.embed(text).dense

    def embed_sparse(self, text: str) -> tuple[list[int], list[float]]:
        embedding = self.embed(text)
        return embedding.sparse_indices, embedding.sparse_values


class OpenAIEmbedder:
    """Optional hosted dense embeddings via an OpenAI-compatible API.

    Sparse weights still come from :class:`HashingEmbedder` because BM25-style
    lexical signals remain valuable for symbol-name precision.
    """

    name = "openai"

    def __init__(self, api_key: str, model: str = "text-embedding-3-small", base_url: str = "https://api.openai.com/v1") -> None:
        try:
            import httpx
        except ImportError as error:  # pragma: no cover
            raise RuntimeError("httpx is required for the openai embedding provider") from error
        self._client = httpx.Client(timeout=30.0)
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._fallback = HashingEmbedder()
        self.dimensions = 1536

    def embed(self, text: str) -> Embedding:
        try:
            response = self._client.post(
                f"{self._base_url}/embeddings",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self._model, "input": text[:8000]},
            )
            response.raise_for_status()
            vector = [float(value) for value in response.json()["data"][0]["embedding"]]
            self.dimensions = len(vector)
        except Exception:  # noqa: BLE001 - degrade gracefully to hashing
            return self._fallback.embed(text)
        lexical = self._fallback.embed(text)
        return Embedding(
            dense=_normalize(vector),
            sparse_indices=lexical.sparse_indices,
            sparse_values=lexical.sparse_values,
            tokens=lexical.tokens,
        )

    def embed_dense(self, text: str) -> list[float]:
        return self.embed(text).dense

    def embed_sparse(self, text: str) -> tuple[list[int], list[float]]:
        embedding = self.embed(text)
        return embedding.sparse_indices, embedding.sparse_values


def build_embedder(provider: str, dimensions: int, openai_api_key: str = "", openai_model: str = "text-embedding-3-small", openai_base_url: str = "https://api.openai.com/v1"):
    """Instantiate the configured embedding provider with graceful fallback."""
    if provider == "openai" and openai_api_key:
        return OpenAIEmbedder(api_key=openai_api_key, model=openai_model, base_url=openai_base_url)
    return HashingEmbedder(dimensions=dimensions)


def cosine(left: list[float], right: list[float]) -> float:
    """Cosine similarity for two equal-length vectors."""
    dot = 0.0
    length = min(len(left), len(right))
    for index in range(length):
        dot += left[index] * right[index]
    return dot


def bm25_score(
    query_tokens: list[str],
    document_tokens: dict[str, int],
    document_length: int,
    average_length: float,
    idf: dict[str, float],
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    """Okapi BM25 for one document given pre-computed query IDFs."""
    if not query_tokens or document_length == 0:
        return 0.0
    score = 0.0
    for token in set(query_tokens):
        tf = document_tokens.get(token, 0)
        if tf == 0:
            continue
        numerator = tf * (k1 + 1.0)
        denominator = tf + k1 * (1.0 - b + b * (document_length / (average_length or 1.0)))
        score += idf.get(token, 0.0) * (numerator / denominator)
    return score

