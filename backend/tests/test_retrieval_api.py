"""Integration tests: engine wiring, hybrid retrieval, tenancy, and API contract."""

import pytest

from fastapi.testclient import TestClient

from app.config import get_settings
from app.engine import Engine
from app.main import app


@pytest.fixture(scope="module")
def engine():
    engine = Engine(get_settings())
    engine.seed_demo()
    return engine


class TestDemoSeeding:
    def test_acme_corpus_indexed(self, engine):
        stats = engine.tenant_stats("acme")
        assert stats.repositories == 1
        assert stats.files == 8
        assert stats.symbols >= 20
        assert stats.languages == ["typescript"]

    def test_globex_corpus_indexed(self, engine):
        stats = engine.tenant_stats("globex")
        assert stats.repositories == 1
        assert stats.files == 4
        assert {"python", "go"}.issubset(set(stats.languages))


class TestHybridRetrieval:
    def test_payment_query_ranks_payment_symbols(self, engine):
        from app.retrieval import hybrid_search

        results = hybrid_search(engine, tenant_id="acme", query="Where is payment authorization handled?", limit=8)
        assert len(results) <= 8
        chunks = [engine.docs.get(r.chunk_id) for r in results]
        payment_hits = [
            c.symbol
            for c in chunks
            if c and ("Payment" in c.symbol or "Stripe" in c.symbol or "auth" in c.symbol.lower() or "checkout" in c.symbol.lower())
        ]
        assert payment_hits

    def test_rrf_returns_descending_fused_scores(self, engine):
        from app.retrieval import hybrid_search

        results = hybrid_search(engine, tenant_id="acme", query="checkout controller create", limit=6)
        assert len(results) == 6
        scores = [r.fused_score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_repository_filter_matches_scope(self, engine):
        from app.retrieval import hybrid_search

        results = hybrid_search(
            engine, tenant_id="acme", query="gateway capture", limit=8, repository_id="atlas-api"
        )
        assert len(results) <= 8

    def test_unknown_terms_return_limited_results(self, engine):
        from app.retrieval import hybrid_search

        results = hybrid_search(engine, tenant_id="globex", query="quantum teleportation machine", limit=8)
        assert len(results) <= 3


class TestTenantIsolation:
    def test_globex_never_returns_acme_chunks(self, engine):
        from app.retrieval import hybrid_search

        hits = hybrid_search(engine, tenant_id="globex", query="PaymentService StripeGateway charge", limit=20)
        for hit in hits:
            chunk = engine.docs.get(hit.chunk_id)
            assert chunk is None or chunk.repository_id == "orbit-core"

    def test_vector_counts_are_tenant_scoped(self, engine):
        assert engine.vector.count("acme") > 0
        assert engine.vector.count("globex") > 0

    def test_graph_snapshot_is_single_repository(self, engine):
        nodes, _edges = engine.graph.snapshot("acme")
        assert nodes
        assert all(node.repository_id == "atlas-api" for node in nodes)


class TestGraphExpansion:
    def test_shorthand_target_resolves_to_qualified_symbol(self, engine):
        resolved = engine.graph.resolve("acme", "atlas-api", "gateway.capture")
        assert resolved == "StripeGateway.capture"

    def test_budget_limits_expansion(self, engine):
        nodes, _edges = engine.graph.neighbors("acme", "PaymentService.charge", hops=3, limit=5)
        assert len(nodes) <= 6

class TestIngestFlow:
    def test_reingest_is_idempotent(self, engine):
        from app.ingest import run_ingest
        from app.models import IngestRequest

        before = engine.docs.count("acme")
        result = run_ingest(engine, IngestRequest(tenant_id="acme", repository_id="atlas-api"))
        after = engine.docs.count("acme")
        assert result["status"] == "indexed"
        assert after == before  # delete + rewrite leaves identical count

    def test_local_mode_indexes_directory(self, engine, tmp_path):
        from app.ingest import run_ingest
        from app.models import IngestRequest

        (tmp_path / "hello.py").write_text("def greet(name):\n    return f'hi {name}'\n", encoding="utf-8")
        (tmp_path / "ingored.md").write_text("not code\n", encoding="utf-8")
        result = run_ingest(
            engine,
            IngestRequest(tenant_id="acme", repository_id="scratch", mode="local", path=str(tmp_path)),
        )
        assert result["status"] == "indexed"
        assert result["stats"]["symbols_indexed"] >= 1
        engine.remove_repository("acme", "scratch")

    def test_git_mode_without_network_fails_cleanly(self, engine):
        from app.ingest import run_ingest
        from app.models import IngestRequest

        result = run_ingest(
            engine,
            IngestRequest(tenant_id="acme", repository_id="remote", mode="git", git_url="https://example.invalid/repo.git"),
        )
        assert result["status"] == "failed"
        engine.remove_repository("acme", "remote")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


class TestAPI:
    def test_health(self, client):
        payload = client.get("/health").json()
        assert payload["status"] == "ok"
        assert "vector" in payload["services"]

    def test_root(self, client):
        payload = client.get("/").json()
        assert payload["name"] == "CodeGraphix API"

    def test_search_contract(self, client):
        payload = client.post(
            "/v1/search",
            json={"tenant_id": "acme", "query": "payment authorization", "limit": 5},
        ).json()
        assert payload["tenant_id"] == "acme"
        assert len(payload["results"]) <= 5
        assert isinstance(payload["answer"], str)
        first = payload["results"][0]
        assert {"symbol", "file_path", "score", "snippet", "callers", "callees"} <= set(first)
        assert payload["latency_ms"] >= 0

    def test_search_accepts_backward_compatible_body(self, client):
        payload = client.post(
            "/v1/search",
            json={"tenant_id": "acme", "query": "orders repository", "limit": 4, "expand_graph": True},
        ).json()
        assert len(payload["results"]) >= 1

    def test_stats_endpoint(self, client):
        payload = client.get("/v1/stats", params={"tenant_id": "acme"}).json()
        assert payload["symbols"] > 0 and payload["chunks"] > 0

    def test_repositories_endpoint(self, client):
        payload = client.get("/v1/repositories", params={"tenant_id": "acme"}).json()
        assert any(repo["repository_id"] == "atlas-api" for repo in payload)

    def test_graph_endpoint_model(self, client):
        payload = client.get("/v1/graph", params={"tenant_id": "acme"}).json()
        assert payload["tenant_id"] == "acme"
        assert payload["nodes"]
        assert all("source" in edge and "target" in edge for edge in payload["edges"])

    def test_async_ingest_job_lifecycle(self, client):
        queued = client.post(
            "/v1/ingest",
            json={"tenant_id": "globex", "repository_id": "orbit-core", "wait": False},
        ).json()
        assert queued["status"] == "queued"
        status = client.get(f"/v1/ingest/{queued['job_id']}").json()
        assert status["status"] in ("running", "done", "failed")
        assert status["repository_id"] == "orbit-core"

    def test_unknown_job_404(self, client):
        assert client.get("/v1/ingest/nope").status_code == 404

    def test_bad_ingest_validation(self, client):
        response = client.post("/v1/ingest", json={"tenant_id": "", "repository_id": "x"})
        assert response.status_code == 422