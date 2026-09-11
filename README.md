# CodeGraphix

## Multi-Tenant Codebase RAG with Dynamic AST Chunking and Dependency Graph Injection

CodeGraphix is a code intelligence platform for teams that need reliable answers from large, multi-repository codebases. It combines syntax-aware code chunks, sparse-dense retrieval, and a dependency graph so an answer is grounded in the implementation that surrounds a matching symbol.

A conventional code search pipeline often retrieves the file containing a keyword and stops. That breaks down when the relevant behavior is split across a controller, service, repository, SDK adapter, and configuration layer. CodeGraphix treats functions, methods, classes, and modules as first-class knowledge units. It stores their source, metadata, embeddings, and relationships, then expands the highest scoring matches through a graph before assembling the context passed to a language model.

> The repository ships with a deterministic demo mode. The browser and API are immediately usable without model credentials or running databases. Docker Compose provides the production-shaped Qdrant and Neo4j services when you are ready to connect real indexing.

## What Is Included

- A Next.js App Router dashboard with a focused codebase question workflow.
- A FastAPI API with tenant-scoped search and ingestion contracts.
- Dynamic AST chunking architecture designed around symbol-level chunks rather than arbitrary line windows.
- Hybrid retrieval boundary for dense embeddings plus lexical or sparse signals.
- Neo4j graph model for callers, callees, imports, inheritance, and symbol ownership.
- Qdrant and Neo4j Docker services with persistent volumes.
- A deterministic local fallback dataset for demos, UI review, and contract testing.
- Responsive interface that exposes retrieved AST chunks and graph-injected context.

## Why AST Chunks

Line-based chunking is simple, but it creates several failure modes:

1. A function can be cut in half at a chunk boundary.
2. A class definition can become separated from the methods that explain its behavior.
3. Retrieval metadata loses the symbol name, declaration kind, and scope needed for filtering.
4. A matching line can be returned without the caller or callee that gives it meaning.

An AST-aware chunker walks a parsed syntax tree and creates chunks at semantic boundaries. The intended chunk record contains the source span, qualified symbol name, declaration kind, language, repository, branch or commit, tenant, and relationships discovered during parsing. Large declarations can be split by child nodes while preserving the parent symbol and an overlap-free source range.

## Architecture

```text
                         +-----------------------+
                         |       Next.js UI       |
                         | tenant + query + graph |
                         +-----------+-----------+
                                     |
                                     | REST /v1/search
                                     v
                         +-----------------------+
                         |      FastAPI API       |
                         | auth, tenant scope,   |
                         | retrieval orchestration|
                         +-----+------------+----+
                               |            |
                 dense + sparse|            | graph expansion
                               v            v
                    +----------------+  +----------------+
                    |     Qdrant     |  |     Neo4j      |
                    | vector points  |  | symbol graph   |
                    +----------------+  +----------------+
                               ^            ^
                               |            |
                         +-----+------------+----+
                         | AST ingestion worker   |
                         | Tree-sitter parsers    |
                         | symbol + edge writer   |
                         +------------------------+
```

### Query path

1. Authenticate the request and resolve its tenant scope.
2. Normalize the query and create dense and sparse representations.
3. Run hybrid retrieval in the tenant and optional repository scope.
4. Deduplicate results at the symbol level.
5. Traverse Neo4j for a bounded number of caller and callee hops.
6. Fetch the related symbol definitions and rank the expanded context.
7. Assemble an LLM prompt containing the question, primary chunks, and graph context.
8. Return citations, scores, symbol metadata, and the answer stream.

### Ingestion path

1. Accept a repository identifier, commit SHA, and tenant context.
2. Clone or fetch the repository in an isolated worker.
3. Select a Tree-sitter grammar from the file extension.
4. Walk the AST and emit declarations, references, imports, and inheritance edges.
5. Apply dynamic chunking rules to keep complete semantic units within the embedding budget.
6. Generate dense embeddings and sparse token weights.
7. Upsert points into a tenant-partitioned Qdrant collection.
8. Upsert symbols and relationships into Neo4j with an idempotent commit key.
9. Publish indexing statistics and mark the repository revision ready.

## Tenancy Model

Every durable record is scoped by `tenant_id`. A production implementation should enforce tenant scope in three places:

- Request authentication: derive the tenant from the signed identity, never trust a user-provided tenant for authorization.
- Qdrant filtering: include a mandatory tenant payload filter on every search and scroll operation.
- Neo4j queries: include tenant ownership in node patterns and relationship traversals.

Repository IDs are unique within a tenant. A useful symbol identity is `(tenant_id, repository_id, commit_sha, fully_qualified_name, start_byte)`. This makes re-indexing a commit idempotent and keeps historical revisions available for audit or branch-aware queries.

## Storage Design

### Qdrant point payload

```json
{
  "id": "stable-symbol-or-content-id",
  "dense": [0.01, -0.02, 0.34],
  "sparse": {"indices": [12, 42], "values": [0.8, 0.4]},
  "payload": {
    "tenant_id": "acme",
    "repository_id": "atlas-api",
    "commit_sha": "abc123",
    "symbol": "PaymentService.charge",
    "kind": "method",
    "language": "typescript",
    "file_path": "src/payments/service.ts",
    "start_line": 18,
    "end_line": 25,
    "content": "..."
  }
}
```

Use a named-vector collection when the Qdrant deployment supports it. A dense vector captures semantic similarity, while a sparse vector rewards exact symbol names, framework methods, identifiers, and error codes. Reciprocal rank fusion or a weighted score can combine both rankings.

### Neo4j graph

Suggested labels and relationships:

- `(:Tenant)-[:OWNS]->(:Repository)`
- `(:Repository)-[:CONTAINS]->(:File)`
- `(:File)-[:DECLARES]->(:Symbol)`
- `(:Symbol)-[:CALLS]->(:Symbol)`
- `(:Symbol)-[:IMPORTS]->(:Symbol)`
- `(:Symbol)-[:EXTENDS|IMPLEMENTS]->(:Symbol)`

Relationships should carry commit and source location metadata when the graph retains multiple revisions. Traversals should enforce a maximum depth and result budget to avoid turning a focused question into an entire application dump.

## Local Development

### Prerequisites

- Node.js 20 or newer
- Python 3.12 or newer
- Docker Desktop with Compose
- Optional: an embedding provider and LLM API key for a production answer layer

### Frontend

```powershell
npm install
npm run dev
```

Open `http://localhost:3000`. The UI starts with a demo retrieval and will call the API when it is available.

### API

```powershell
python -m venv .venv
.venv\\Scripts\\Activate.ps1
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload --port 8000
```

The API exposes:

- `GET /health`
- `POST /v1/ingest`
- `POST /v1/search`

Example search request:

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/v1/search `
  -ContentType 'application/json' `
  -Body '{"tenant_id":"acme","query":"Where is payment authorization handled?","limit":8,"expand_graph":true}'
```

### Docker Compose

```powershell
docker compose up --build
```

Services:

- Web: `http://localhost:3000`
- API: `http://localhost:8000`
- Qdrant: `http://localhost:6333/dashboard`
- Neo4j Browser: `http://localhost:7474` with `neo4j` / `codegraphix`

The demo API intentionally reports Qdrant and Neo4j as fallback services until adapters are connected. This lets interface development and API contract work proceed while infrastructure is unavailable.

## Production Hardening Checklist

- Add OIDC or SSO authentication and derive tenant membership from claims.
- Store secrets in a managed secret store; never commit `.env` files.
- Use separate Qdrant collections or strict payload filters per tenant.
- Add Neo4j constraints for tenant, repository, commit, and symbol identities.
- Run ingestion in a queue with retry, cancellation, and progress events.
- Persist parser errors and unsupported file types as inspectable job warnings.
- Add an embedding cache keyed by content hash and embedding model version.
- Redact secrets and credentials before embedding source text.
- Add answer citations with repository, commit, file path, and line range.
- Apply graph traversal budgets, query timeouts, and prompt token limits.
- Add structured logs, metrics, tracing, rate limits, and audit events.
- Pin grammars and model versions to make indexing reproducible.

## Testing Strategy

The first test layer should validate pure AST extraction and chunk boundaries with fixture repositories. The second should test hybrid rank fusion with known queries. The third should test tenant isolation by attempting searches across two tenants. The fourth should test graph expansion with caller/callee fixtures. Finally, run API contract tests and a browser smoke test against Docker Compose.

A robust evaluation set should include questions whose answer is not present in the highest-ranked file alone. Examples include: where a value is transformed before persistence, which controller triggers a worker, what implementation satisfies an interface, and which configuration value changes a provider's behavior.

## Repository Layout

```text
CodeGraphix/
├── backend/
│   ├── app/
│   │   ├── config.py       # settings and environment parsing
│   │   ├── main.py         # FastAPI routes and middleware
│   │   ├── models.py       # request and response contracts
│   │   └── services.py     # deterministic retrieval demo and embeddings
│   ├── Dockerfile
│   └── requirements.txt
├── src/app/
│   ├── globals.css         # visual system and responsive layout
│   ├── layout.tsx         # metadata and root layout
│   └── page.tsx           # code intelligence workspace
├── docker-compose.yml
├── Dockerfile
└── README.md
```

## License

This project is an internal starter implementation. Add your organization's license and contribution policy before distributing it externally.
