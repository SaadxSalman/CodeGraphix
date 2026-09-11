from time import perf_counter
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .models import HealthResponse, IngestRequest, SearchRequest, SearchResponse, SearchResult
from .services import search_demo

app = FastAPI(title="CodeGraphix API", version="0.1.0", description="Multi-tenant codebase RAG API")
app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", services={"api": "ready", "qdrant": "demo fallback", "neo4j": "demo fallback"})


@app.post("/v1/ingest")
def ingest(request: IngestRequest) -> dict:
    return {"status": "queued", "tenant_id": request.tenant_id, "repository_id": request.repository_id, "message": "Repository accepted for AST indexing."}


@app.post("/v1/search", response_model=SearchResponse)
def search(request: SearchRequest) -> SearchResponse:
    started = perf_counter()
    ranked = search_demo(request.tenant_id, request.query, request.limit)
    results = [SearchResult(id=item.id, symbol=item.symbol, file_path=item.file_path, language=item.language, kind=item.kind, score=round(score, 2), snippet=item.snippet, callers=list(item.callers), callees=list(item.callees)) for item, score in ranked]
    context_symbols = []
    if request.expand_graph:
        context_symbols = list(dict.fromkeys([edge for result in results for edge in result.callers + result.callees]))
    return SearchResponse(query=request.query, tenant_id=request.tenant_id, results=results, context_symbols=context_symbols, latency_ms=round((perf_counter() - started) * 1000, 2), mode="demo-hybrid-graph")
