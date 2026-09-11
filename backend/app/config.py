"""Environment-driven configuration for the CodeGraphix API.

Every value can be overridden with an environment variable whose name matches
the field (case-insensitive), e.g. ``QDRANT_URL``, ``NEO4J_URI``, or
``CHUNK_MAX_CHARS``. A local ``.env`` file is honoured when present.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for ingestion, retrieval, and storage backends."""

    # --- infrastructure endpoints --------------------------------------------
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "codegraphix"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "codegraphix"
    cors_origins: str = "http://localhost:3000"

    # --- storage backend selection --------------------------------------------
    # "auto" probes Qdrant / Neo4j at startup and transparently falls back to
    # the deterministic in-memory engine when a service is unreachable.
    # Explicit values ("memory", "qdrant", "neo4j") pin a backend.
    vector_backend: str = "auto"
    graph_backend: str = "auto"

    # --- retrieval -------------------------------------------------------------
    embedding_dimensions: int = 256
    embedding_provider: str = "hashing"  # hashing | openai
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_embedding_model: str = "text-embedding-3-small"
    rrf_k: int = 60
    search_candidates: int = 64
    dense_relevance_floor: float = 0.08
    graph_max_hops: int = 1
    graph_max_symbols: int = 24

    # --- chunking ---------------------------------------------------------------
    chunk_max_chars: int = 1600
    chunk_min_chars: int = 40

    # --- ingestion ----------------------------------------------------------------
    ingest_max_files: int = 2000
    ingest_max_file_bytes: int = 524288
    ignore_dirs: str = (
        ".git,node_modules,.venv,venv,__pycache__,.next,dist,build,out,"
        "target,vendor,coverage,.pytest_cache,.mypy_cache,.idea,.vscode,.gradle"
    )
    seed_tenants: str = "acme,globex"

    # --- optional LLM answer layer -------------------------------------------------
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 30.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def ignore_dir_list(self) -> list[str]:
        return [item.strip() for item in self.ignore_dirs.split(",") if item.strip()]

    @property
    def seed_tenant_list(self) -> list[str]:
        return [item.strip() for item in self.seed_tenants.split(",") if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

