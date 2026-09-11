"""Engine: the composition root wiring embedder, stores, and services.

The engine owns the storage backends (selected at startup from settings with
graceful fallback to the deterministic in-memory implementations), the
repository registry, ingestion job bookkeeping, and the demo seeding that
makes the API immediately useful after ``uvicorn`` starts.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from .config import Settings, get_settings
from .embeddings import build_embedder
from .models import GraphNode, GraphSnapshot, RepositoryInfo, TenantStats
from .stores import build_graph_store, build_vector_store


class RepositoryEntry:
    """Registry entry describing one indexed repository revision."""

    def __init__(self, **kwargs: Any) -> None:
        self.tenant_id: str = kwargs["tenant_id"]
        self.repository_id: str = kwargs["repository_id"]
        self.name: str = kwargs["name"]
        self.commit_sha: str = kwargs["commit_sha"]
        self.files: int = kwargs["files"]
        self.chunks: int = kwargs["chunks"]
        self.symbols: int = kwargs["symbols"]
        self.edges: int = kwargs["edges"]
        self.languages: list[str] = kwargs["languages"]
        self.indexed_at: float = kwargs["indexed_at"]
        self.mode: str = kwargs.get("mode", "unknown")

    def to_info(self) -> RepositoryInfo:
        return RepositoryInfo(
            tenant_id=self.tenant_id,
            repository_id=self.repository_id,
            name=self.name,
            commit_sha=self.commit_sha,
            files=self.files,
            symbols=self.symbols,
            chunks=self.chunks,
            edges=self.edges,
            languages=self.languages,
            indexed_at=self.indexed_at,
        )


class Engine:
    """Composition root for the retrieval pipeline."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.embedder = build_embedder(
            provider=self.settings.embedding_provider,
            dimensions=self.settings.embedding_dimensions,
            openai_api_key=self.settings.openai_api_key,
            openai_model=self.settings.openai_embedding_model,
            openai_base_url=self.settings.openai_base_url,
        )
        self.vector = build_vector_store(self.settings)
        self.graph = build_graph_store(self.settings)
        from .stores import MemoryDocStore

        self.docs = MemoryDocStore()
        self.repositories: dict[tuple[str, str], RepositoryEntry] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.started_at = time.time()

    # -- repository registry ------------------------------------------------------

    def register_repository(self, **kwargs: Any) -> RepositoryEntry:
        entry = RepositoryEntry(
            tenant_id=kwargs["tenant_id"],
            repository_id=kwargs["repository_id"],
            name=kwargs["name"],
            commit_sha=kwargs["commit_sha"],
            files=kwargs["files"],
            chunks=kwargs["chunks"],
            symbols=kwargs["symbols"],
            edges=kwargs["edges"],
            languages=kwargs["languages"],
            indexed_at=time.time(),
            mode=kwargs.get("mode", "unknown"),
        )
        self.repositories[(entry.tenant_id, entry.repository_id)] = entry
        return entry

    def remove_repository(self, tenant_id: str, repository_id: str) -> bool:
        existed = (tenant_id, repository_id) in self.repositories
        self.vector.delete_repository(tenant_id, repository_id)
        self.graph.delete_repository(tenant_id, repository_id)
        self.docs.delete_repository(tenant_id, repository_id)
        self.repositories.pop((tenant_id, repository_id), None)
        return existed

    def list_repositories(self, tenant_id: str) -> list[RepositoryInfo]:
        return [
            entry.to_info()
            for (tenant, _repo), entry in sorted(self.repositories.items())
            if tenant == tenant_id
        ]

    def tenant_stats(self, tenant_id: str) -> TenantStats:
        entries = [entry for (tenant, _repo), entry in self.repositories.items() if tenant == tenant_id]
        symbol_count, edge_count = self.graph.stats(tenant_id)
        return TenantStats(
            tenant_id=tenant_id,
            repositories=len(entries),
            files=sum(entry.files for entry in entries),
            symbols=symbol_count,
            chunks=self.docs.count(tenant_id),
            edges=edge_count,
            languages=sorted({language for entry in entries for language in entry.languages}),
        )

    # -- jobs ---------------------------------------------------------------------

    def start_job(self, tenant_id: str, repository_id: str) -> str:
        job_id = uuid.uuid4().hex[:12]
        self.jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "tenant_id": tenant_id,
            "repository_id": repository_id,
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
            "result": None,
        }
        return job_id

    def finish_job(self, job_id: str, status: str, error: str | None = None, result: dict | None = None) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            return
        job["status"] = status
        job["finished_at"] = time.time()
        job["error"] = error
        job["result"] = result

    def job_status(self, job_id: str) -> dict[str, Any] | None:
        return self.jobs.get(job_id)

    # -- health and graph snapshot ---------------------------------------------------

    def health(self) -> dict[str, Any]:
        from .stores import probe_neo4j, probe_qdrant

        services = {
            "api": "ready",
            "vector": self.vector.backend,
            "graph": self.graph.backend,
        }
        if self.vector.backend == "memory":
            reachable = probe_qdrant(self.settings.qdrant_url)
            services["qdrant_fallback"] = "available" if reachable else "unreachable (in-memory engine active)"
        if self.graph.backend == "memory":
            reachable = probe_neo4j(self.settings.neo4j_uri, self.settings.neo4j_user, self.settings.neo4j_password)
            services["neo4j_fallback"] = "available" if reachable else "unreachable (in-memory engine active)"
        return {
            "status": "ok",
            "services": services,
            "embedding": self.embedder.name,
            "version": "1.0.0",
        }

    def graph_snapshot(self, tenant_id: str, repository_id: str | None = None, limit: int = 64) -> GraphSnapshot:
        from .models import GraphEdge as GraphEdgeModel

        nodes, edges = self.graph.snapshot(tenant_id, repository_id, limit)
        return GraphSnapshot(
            tenant_id=tenant_id,
            repository_id=repository_id,
            nodes=[
                GraphNode(
                    id=f"{node.repository_id}:{node.name}",
                    label=node.name,
                    kind=node.kind,
                    repository_id=node.repository_id,
                    file_path=node.file_path,
                    external=False,
                )
                for node in nodes
            ],
            edges=[
                GraphEdgeModel(source=edge.source, relation=edge.relation, target=edge.target, resolved=edge.resolved)
                for edge in edges
            ],
        )

    # -- seeding -------------------------------------------------------------------

    def seed_demo(self) -> list[str]:
        """Index the built-in demo corpus for every configured tenant."""
        from .ingest import run_ingest
        from .models import IngestRequest

        seeded: list[str] = []
        for tenant_id in self.settings.seed_tenant_list:
            request = IngestRequest(
                tenant_id=tenant_id,
                repository_id="atlas-api" if tenant_id == "acme" else "orbit-core",
            )
            result = run_ingest(self, request)
            if result["status"] == "indexed":
                seeded.append(tenant_id)
        return seeded


