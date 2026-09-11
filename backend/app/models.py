from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    repository_id: str | None = None
    limit: int = Field(default=8, ge=1, le=20)
    expand_graph: bool = True


class SearchResult(BaseModel):
    id: str
    symbol: str
    file_path: str
    language: str
    kind: str
    score: float
    snippet: str
    callers: list[str] = []
    callees: list[str] = []


class SearchResponse(BaseModel):
    query: str
    tenant_id: str
    results: list[SearchResult]
    context_symbols: list[str]
    latency_ms: float
    mode: str


class IngestRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    repository_name: str = Field(min_length=1)


class HealthResponse(BaseModel):
    status: str
    services: dict[str, str]
