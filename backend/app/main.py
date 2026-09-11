"""CodeGraphix FastAPI application.

Endpoints:
- ``GET  /health`` — service status including active backend selection.
- ``POST /v1/ingest`` — index a repository (sample | local | git).
- ``GET  /v1/ingest/{job_id}`` — poll an asynchronous ingestion job.
- ``POST /v1/search`` — hybrid retrieval with graph expansion and answers.
- ``GET  /v1/repositories`` — indexed repositories for a tenant.
- ``DELETE /v1/repositories/{tenant_id}/{repository_id}`` — tenant-scoped removal.
- ``GET  /v1/stats`` — per-tenant index statistics.
- ``GET  /v1/graph`` — symbol graph snapshot for visualization.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from .answering import synthesize_answer
from .config import get_settings
from .engine import Engine
from .ingest import run_ingest, spawn_ingest
from .models import (
    GraphSnapshot,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    JobStatus,
    RepositoryInfo,
    RootResponse,
    SearchRequest,
    SearchResponse,
    TenantStats,
)
from .retrieval import expand_graph, hydrate_results, hybrid_search

API_VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(application: FastAPI):
    settings = get_settings()
    engine = Engine(settings)
    seeded = engine.seed_demo()
    application.state.engine = engine
    application.state.seeded_tenants = seeded
    yield


app = FastAPI(
    title="CodeGraphix API",
    version=API_VERSION,
    description="Multi-tenant codebase RAG with AST chunking, hybrid retrieval, and dependency graph expansion.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_engine(request: Request) -> Engine:
    return request.app.state.engine

@app.get("/", response_model=RootResponse)
def root() -> RootResponse:
    return RootResponse(
        name="CodeGraphix API",
        version=API_VERSION,
        description="Multi-tenant codebase RAG: AST chunks + hybrid retrieval + dependency graph.",
        endpoints=["/health", "/v1/ingest", "/v1/search", "/v1/repositories", "/v1/stats", "/v1/graph", "/docs"],
    )


@app.get("/health", response_model=HealthResponse)
def health(engine: Engine = Depends(get_engine)) -> HealthResponse:
    payload = engine.health()
    return HealthResponse(**payload)


@app.post("/v1/ingest", response_model=IngestResponse)
def ingest(request: IngestRequest, engine: Engine = Depends(get_engine)) -> IngestResponse:
    if request.wait:
        result = run_ingest(engine, request)
        return IngestResponse(**result)
    job_id = engine.start_job(request.tenant_id, request.repository_id)
    spawn_ingest(engine, request, job_id)
    return IngestResponse(
        status="queued",
        tenant_id=request.tenant_id,
        repository_id=request.repository_id,
        mode=request.mode,
        job_id=job_id,
        message="Ingestion started; poll /v1/ingest/{job_id} for progress.",
    )


@app.get("/v1/ingest/{job_id}", response_model=JobStatus)
def ingest_status(job_id: str, engine: Engine = Depends(get_engine)) -> JobStatus:
    job = engine.job_status(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    return JobStatus(**job)


@app.post("/v1/search", response_model=SearchResponse)
def search(request: SearchRequest, engine: Engine = Depends(get_engine)) -> SearchResponse:
    started = perf_counter()
    hybrid = hybrid_search(
        engine,
        tenant_id=request.tenant_id,
        query=request.query,
        limit=request.limit,
        repository_id=request.repository_id,
        language=request.language,
        kind=request.kind,
    )
    results = hydrate_results(engine, request.tenant_id, hybrid)

    context_symbols: list[str] = []
    graph_edges = []
    if request.expand_graph:
        context_symbols, graph_edges = expand_graph(
            engine,
            tenant_id=request.tenant_id,
            results=results,
            repository_id=request.repository_id,
        )

    answer = ""
    citations = []
    if request.generate_answer:
        answer, citations = synthesize_answer(
            request.query,
            results,
            context_symbols,
            engine.settings,
            engine.vector.backend,
            engine.graph.backend,
        )

    latency = round((perf_counter() - started) * 1000, 2)
    mode_parts = ["hybrid-rrf", engine.vector.backend, "graph" if request.expand_graph else "no-graph"]
    if request.generate_answer:
        mode_parts.append("answer")
    return SearchResponse(
        query=request.query,
        tenant_id=request.tenant_id,
        results=results,
        context_symbols=context_symbols,
        graph_edges=graph_edges,
        answer=answer,
        citations=citations,
        latency_ms=latency,
        mode="+".join(mode_parts),
    )


@app.get("/v1/repositories", response_model=list[RepositoryInfo])
def repositories(
    tenant_id: str = Query(min_length=1),
    engine: Engine = Depends(get_engine),
) -> list[RepositoryInfo]:
    return engine.list_repositories(tenant_id)


@app.delete("/v1/repositories/{tenant_id}/{repository_id}")
def delete_repository(tenant_id: str, repository_id: str, engine: Engine = Depends(get_engine)) -> dict:
    removed = engine.remove_repository(tenant_id, repository_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Repository not found for this tenant.")
    return {"status": "removed", "tenant_id": tenant_id, "repository_id": repository_id}


@app.get("/v1/stats", response_model=TenantStats)
def stats(tenant_id: str = Query(min_length=1), engine: Engine = Depends(get_engine)) -> TenantStats:
    return engine.tenant_stats(tenant_id)


@app.get("/v1/graph", response_model=GraphSnapshot)
def graph(
    tenant_id: str = Query(min_length=1),
    repository_id: str | None = None,
    limit: int = Query(default=64, ge=1, le=500),
    engine: Engine = Depends(get_engine),
) -> GraphSnapshot:
    return engine.graph_snapshot(tenant_id, repository_id, limit)


