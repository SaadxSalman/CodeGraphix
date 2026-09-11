"""HTTP contracts for the CodeGraphix API.

The request models are backwards compatible with the original 0.1 contract;
newer fields are optional and default to sensible behaviour so existing
clients keep working.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# --- search ---------------------------------------------------------------------


class SearchRequest(BaseModel):
    tenant_id: str = Field(min_length=1, description="Tenant scope for the search.")
    query: str = Field(min_length=1, description="Natural-language question or code query.")
    repository_id: str | None = Field(default=None, description="Optional repository filter.")
    language: str | None = Field(default=None, description="Optional language filter, e.g. 'python'.")
    kind: str | None = Field(default=None, description="Optional symbol kind filter, e.g. 'method'.")
    limit: int = Field(default=8, ge=1, le=50, description="Maximum number of chunks returned.")
    expand_graph: bool = Field(default=True, description="Expand matches through the dependency graph.")
    generate_answer: bool = Field(default=True, description="Assemble a cited answer from the context.")


class Citation(BaseModel):
    symbol: str
    file_path: str
    start_line: int
    end_line: int
    repository_id: str


class SearchResult(BaseModel):
    id: str
    symbol: str
    file_path: str
    language: str
    kind: str
    repository_id: str
    score: float = Field(description="Fused hybrid score in [0, 1].")
    dense_score: float = Field(default=0.0, description="Normalised dense (semantic) score.")
    sparse_score: float = Field(default=0.0, description="Normalised sparse (lexical/BM25) score.")
    start_line: int
    end_line: int
    snippet: str
    callers: list[str] = []
    callees: list[str] = []
    imports: list[str] = []
    doc: str | None = None


class GraphEdge(BaseModel):
    source: str
    relation: Literal["CALLS", "IMPORTS", "EXTENDS", "IMPLEMENTS"]
    target: str
    resolved: bool = Field(default=True, description="True when target matches an indexed symbol.")


class SearchResponse(BaseModel):
    query: str
    tenant_id: str
    results: list[SearchResult]
    context_symbols: list[str]
    graph_edges: list[GraphEdge]
    answer: str
    citations: list[Citation]
    latency_ms: float
    mode: str = Field(description="Pipeline descriptor, e.g. 'hybrid-rrf+graph+answer'.")


# --- ingestion --------------------------------------------------------------------


class IngestRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    repository_name: str | None = Field(default=None, description="Display name; defaults to repository_id.")
    mode: Literal["sample", "local", "git"] = Field(
        default="sample",
        description=(
            "sample: index the built-in demo corpus. "
            "local: walk a filesystem path (requires `path`). "
            "git: shallow-clone `git_url` (requires git binary)."
        ),
    )
    path: str | None = Field(default=None, description="Absolute or relative path for mode=local.")
    git_url: str | None = Field(default=None, description="Clone URL for mode=git.")
    branch: str | None = Field(default=None, description="Optional branch for mode=git.")
    commit_sha: str | None = Field(default=None, description="Optional revision label; computed when omitted.")
    wait: bool = Field(default=True, description="Block until ingestion finishes; otherwise return a job id.")


class IngestStats(BaseModel):
    files_scanned: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    symbols_indexed: int = 0
    chunks_indexed: int = 0
    edges_indexed: int = 0
    warnings: list[str] = []


class IngestResponse(BaseModel):
    status: Literal["indexed", "queued", "failed"]
    tenant_id: str
    repository_id: str
    commit_sha: str | None = None
    mode: str
    job_id: str | None = None
    message: str
    stats: IngestStats | None = None
    duration_ms: float | None = None


class JobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "done", "failed"]
    tenant_id: str | None = None
    repository_id: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    result: IngestResponse | None = None


# --- health, repositories, stats, graph --------------------------------------------


class HealthResponse(BaseModel):
    status: str
    services: dict[str, str]
    embedding: str
    version: str


class RepositoryInfo(BaseModel):
    tenant_id: str
    repository_id: str
    name: str
    commit_sha: str
    files: int
    symbols: int
    chunks: int
    edges: int
    languages: list[str]
    indexed_at: float


class TenantStats(BaseModel):
    tenant_id: str
    repositories: int
    files: int
    symbols: int
    chunks: int
    edges: int
    languages: list[str]


class GraphNode(BaseModel):
    id: str
    label: str
    kind: str
    repository_id: str
    file_path: str | None = None
    external: bool = False


class GraphSnapshot(BaseModel):
    tenant_id: str
    repository_id: str | None = None
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class ErrorResponse(BaseModel):
    detail: str


class RootResponse(BaseModel):
    name: str
    version: str
    description: str
    endpoints: list[str]

