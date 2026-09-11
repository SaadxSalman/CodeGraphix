# CodeGraphix API

FastAPI service for multi-tenant codebase RAG: AST-aware ingestion, hybrid
retrieval, dependency-graph expansion, and cited answers.

The service runs fully on a deterministic in-memory engine out of the box —
no model credentials, no running databases. When Qdrant and Neo4j are
reachable (or pinned via `VECTOR_BACKEND` / `GRAPH_BACKEND`), the same code
paths use them transparently.

## Quick start

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
uvicorn app.main:app --reload --port 8000   # run from backend/
```

On startup the demo corpora for `acme` (TypeScript) and `globex` (Python+Go)
are indexed automatically. Interactive docs: `http://localhost:8000/docs`.

## Endpoints

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET    | `/health` | Service status and active backends |
| GET    | `/` | API index |
| POST   | `/v1/ingest` | Index a repo (`sample` \| `local` \| `git`) |
| GET    | `/v1/ingest/{job_id}` | Poll an async ingestion job |
| POST   | `/v1/search` | Hybrid search + graph expansion + answer |
| GET    | `/v1/repositories` | Indexed repositories for a tenant |
| DELETE | `/v1/repositories/{tenant}/{repo}` | Remove a repository |
| GET    | `/v1/stats` | Per-tenant index statistics |
| GET    | `/v1/graph` | Symbol graph snapshot |

## Search payload

```json
{
  "tenant_id": "acme",
  "query": "Where is payment authorization handled?",
  "limit": 8,
  "expand_graph": true,
  "generate_answer": true,
  "repository_id": null,
  "language": null,
  "kind": null
}
```

## Tests

```powershell
python -m pytest backend/tests -q
```

Covers AST chunking boundaries, relationship extraction, embedding
determinism, hybrid rank fusion, tenant isolation, graph expansion budgets,
idempotent ingestion, and the full HTTP contract.
