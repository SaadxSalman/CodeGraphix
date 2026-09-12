# CodeGraphix

## Multi-Tenant Codebase RAG with Dynamic AST Chunking and Dependency Graph Injection

CodeGraphix is a code intelligence platform for teams that need reliable answers from large, multi-repository codebases. It combines syntax-aware code chunks, sparse-dense retrieval, and a dependency graph so an answer is grounded in the implementation that surrounds a matching symbol.

A conventional code search pipeline often retrieves the file containing a keyword and stops. That breaks down when the relevant behavior is split across a controller, service, repository, SDK adapter, and configuration layer. CodeGraphix treats functions, methods, classes, and modules as first-class **knowledge units**. It stores their source, metadata, embeddings, and relationships, then expands the highest scoring matches through a graph before assembling the context passed to a language model.

> **The repository ships fully functional.** The deterministic demo mode needs no model credentials and no running databases: `uvicorn` boots with two demo tenants already indexed, and the browser UI talks to it immediately. Docker Compose provides the production-shaped **Qdrant** and **Neo4j** services when you are ready to connect real indexing at scale.

---

## Table of Contents

- [What Is Included](#what-is-included)
- [Why AST Chunks](#why-ast-chunks)
- [Architecture](#architecture)
- [How It Works](#how-it-works)
  - [Query path](#query-path)
  - [Ingestion path](#ingestion-path)
- [Demo Mode vs. Production Backends](#demo-mode-vs-production-backends)
- [Quickstart](#quickstart)
  - [Prerequisites](#prerequisites)
  - [Option A: Docker Compose](#option-a-docker-compose)
  - [Option B: Local development](#option-b-local-development)
  - [First search](#first-search)
- [Configuration Reference](#configuration-reference)
- [API Reference](#api-reference)
- [Retrieval Pipeline Deep Dive](#retrieval-pipeline-deep-dive)
- [Chunking Design](#chunking-design)
- [Storage Design](#storage-design)
  - [Qdrant point payload](#qdrant-point-payload)
  - [Neo4j graph model](#neo4j-graph-model)
- [Tenancy Model](#tenancy-model)
- [Testing Strategy](#testing-strategy)
- [Production Hardening Checklist](#production-hardening-checklist)
- [Repository Layout](#repository-layout)
- [Roadmap](#roadmap)
- [License](#license)

---

## What Is Included

- A **Next.js App Router dashboard** with a focused codebase-question workflow: tenant switcher, live search, cited answers, dynamic dependency graph, and live index statistics.
- A **FastAPI API** with tenant-scoped search and ingestion contracts, async ingestion jobs, repository management, and per-tenant statistics.
- **Dynamic AST chunking** built on tree-sitter grammars (TypeScript, TSX, JavaScript, Python, Go, Java, Rust) with a stdlib `ast` / brace-scanner fallback for offline and niche languages.
- **Hybrid retrieval** that fuses a dense semantic signal (deterministic hashing embeddings by default; OpenAI-compatible models optional) with a sparse BM25 lexical signal using Reciprocal Rank Fusion.
- **Dependency graph expansion** that injects the callers, callees, and type hierarchy surrounding the top matches into the answer context, with strict hop and symbol budgets.
- **Cited answers**: a deterministic extractive answer layer always on, plus an optional LLM summary layer (OpenAI-compatible endpoint) that degrades gracefully to the extractive answer.
- **Qdrant and Neo4j adapters** with automatic fallback: reachable services are used; otherwise the deterministic in-memory engine serves identical data.
- **Docker Compose** with persistent volumes for all four services.
- **A built-in demo corpus** for two tenants — a TypeScript e-commerce backend (`acme/atlas-api`) and a Python + Go data platform (`globex/orbit-core`) — indexed automatically at startup.
- **46 automated tests** across chunking, embeddings, retrieval, tenancy isolation, graph expansion, ingestion idempotency, and the HTTP contract.

---

## Why AST Chunks

Line-based chunking is simple, but it creates several failure modes:

1. A function can be cut in half at a chunk boundary, so the retrieved snippet is unreadable.
2. A class definition can become separated from the methods that explain its behavior.
3. Retrieval metadata loses the symbol name, declaration kind, and scope needed for filtering.
4. A matching line can be returned without the caller or callee that gives it meaning.

An AST-aware chunker walks a parsed syntax tree and creates chunks at semantic boundaries. Each chunk record contains:

- the exact **source span** (byte offsets and line range),
- the **qualified symbol name** (`PaymentService.charge`),
- the **declaration kind** (`class`, `method`, `function`, `interface`, `struct`, `trait`, `impl`, …),
- the **language**, **repository**, and **revision**,
- the **tenant**,
- and the **relationships** discovered during parsing (CALLS, IMPORTS, EXTENDS, IMPLEMENTS).

Large declarations (a 3,000-line method, a giant config class) are split by child nodes into overlap-free parts that stay inside the embedding budget while preserving the parent symbol and line metadata.

---

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
                         |  ingestion jobs,       |
                         |  retrieval orchestration|
                         +-----+------------+----+
                               |            |
               dense + sparse|            | graph expansion
                               v            v
                    +----------------+  +----------------+
                    |     Qdrant     |  |     Neo4j      |
                    | vector points  |  | symbol graph   |
                    | dense+sparse   |  | CALLS/EXTENDS  |
                    +----------------+  +----------------+
                               ^            ^
                               |            |
                         +-----+------------+----+
                         |  AST ingestion worker  |
                         |  tree-sitter parsers   |
                         |  symbol + edge writer  |
                         +------------------------+
```

### Component responsibilities

| Component | Responsibility |
| --------- | -------------- |
| `backend/app/chunking.py` | Tree-sitter AST → `Chunk` records; relationship extraction; fallback chunkers |
| `backend/app/embeddings.py` | Code-aware tokenizer, deterministic hashing embedder, optional OpenAI-compatible dense embeddings, BM25 scorer |
| `backend/app/stores.py` | Memory / Qdrant vector stores; memory / Neo4j graph stores; tenant-scoped doc store; connectivity probes |
| `backend/app/retrieval.py` | Hybrid search, Reciprocal Rank Fusion, hydration, bounded graph expansion |
| `backend/app/answering.py` | Cited extractive answers; optional LLM synthesis; prompt assembly |
| `backend/app/ingest.py` | Repository resolution (`sample` / `local` / `git`), chunk→embed→store pipeline, async jobs |
| `backend/app/engine.py` | Composition root: embedder + stores + registry + job bookkeeping + demo seeding |
| `backend/app/main.py` | FastAPI routes, lifespan startup, CORS |

---
## How It Works

### Query path

1. **Authenticate** the request and resolve its tenant scope (`tenant_id` in the body is trusted only for the demo; a production deployment derives it from a signed identity).
2. **Normalize** the query: code-aware tokenization splits `getUserData` into `getUserData`, `user`, `data`; camelCase and snake_case sub-tokens are kept for the sparse side.
3. **Dense search** scores every chunk's embedding against the query vector (cosine). **Sparse search** runs BM25 over tokenized chunks with precomputed IDF.
4. **Reciprocal Rank Fusion** merges the two rankings, so a chunk that ranks well on *either* signal (semantic or exact-symbol) rises to the top.
5. **Relevance floor**: chunks are only returned when they appear in the sparse ranking or clear a raw dense-cosine threshold — unrelated code stays out.
6. **Deduplicate** results at the chunk level and hydrate the full source snippets, line ranges, docs, plus direct callers and callees.
7. **Expand the graph**: for the top matches, traverse Neo4j (or memory) for a *bounded* number of caller/callee/extends hops, respect a symbol budget, and collect the edges back into the response.
8. **Assemble the answer**: an extractive, fully cited answer is generated deterministically. If `LLM_API_KEY` is configured, the same context is sent to a chat model with a grounded-answer system prompt; any failure falls back to the extractive answer.
9. **Return** the answer, citations, ranked chunks, graph context, and pipeline mode.

### Ingestion path

1. **Resolve the source**:
   - `sample` — built-in demo corpus for the tenant,
   - `local` — walk a filesystem path (language-aware, ignores `node_modules`, `.git`, product build output, …),
   - `git` — shallow-clone a URL with the git binary, then walk the clone.
2. **Detect the language** from the file extension and select a tree-sitter grammar.
3. **Walk the AST** and emit declarations at semantic boundaries with exact byte/line spans, qualified names, docs, calls, imports, and type-hierarchy edges.
4. **Apply dynamic chunking rules** to keep each semantic unit within the embedding budget (large declarations split into overlap-free parts).
5. **Embed each chunk** into a dense vector (hashing or hosted model) and sparse weights (BM25 token frequencies).
6. **Delete the previous revision** of that repository — ingestion is idempotent per `(tenant, repository)` — then upsert vectors, chunk documents, symbol nodes, and edges.
7. **Register the revision** (commit SHA, file/symbol/chunk/edge counts, languages) in the repository registry.
8. **Report stats** back through the job record; `wait=false` requests can poll `/v1/ingest/{job_id}` for progress.

---

## Demo Mode vs. Production Backends

| Concern | Demo mode (default) | Production mode |
| ------- | ------------------- | --------------- |
| Embeddings | Deterministic hashing (offline, reproducible) | `EMBEDDING_PROVIDER=openai` + key |
| Vector store | In-memory brute-force + BM25 | Qdrant named dense + sparse vectors |
| Graph | In-memory adjacency sets | Neo4j `(:Symbol)-[CALLS|EXTENDS|IMPLEMENTS]->(:Symbol)` |
| Answers | Extractive with citations | Optional LLM synthesis (degrades to extractive) |
| Datasets | Two-tenant demo corpus | `local` paths or `git` URLs |

The service boundary means the HTTP contract never changes: swap the backend
by configuration, not by code.

---

## Quickstart

### Prerequisites

- **Node.js 20+** (the Dockerfile uses 22·alpine; Node 26 also works)
- **Python 3.12+** (developed and tested on 3.14)
- **Docker Desktop with Compose** (recommended; optional for demo mode)
- Optional: an OpenAI-compatible API key for hosted embeddings and LLM answers

### Option A: Docker Compose

```powershell
docker compose up --build
```

| Service | URL | Notes |
| ------- | --- | ----- |
| Web | `http://localhost:3000` | Next.js dashboard |
| API | `http://localhost:8000` | FastAPI, seeded on startup |
| Qdrant | `http://localhost:6333/dashboard` | Vector DB |
| Neo4j | `http://localhost:7474` | `neo4j` / `codegraphix` |

The API probes Qdrant and Neo4j at startup. When they are reachable it uses
them; otherwise it transparently runs on the in-memory engine.

### Option B: Local development

**Frontend**

```powershell
npm install
npm run dev
```

Open `http://localhost:3000`. The UI calls `http://localhost:8000` (override
with the `NEXT_PUBLIC_API_URL` environment variable). It runs a demo search
on load and shows live stats, answers, and graph context as soon as the API
answers.

**API**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
uvicorn app.main:app --reload --port 8000   # from backend/
```

Interactive OpenAPI docs: `http://localhost:8000/docs`.

### First search

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/v1/search `
  -ContentType 'application/json' `
  -Body '{"tenant_id":"acme","query":"Where is payment authorization handled?","limit":8,"expand_graph":true}'
```

Or via the UI:

```powershell
curl -s -X POST http://localhost:8000/v1/search `
  -H 'Content-Type: application/json' `
  -d '{"tenant_id":"globex","query":"How does VectorIndexer embed a corpus?","limit":5}'
```

---
## Configuration Reference

All settings are environment variables (a `.env` file in the working
directory is honoured). Copy `.env.example` to get a complete, commented
starting point.

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant HTTP endpoint |
| `QDRANT_COLLECTION` | `codegraphix` | Collection name for dense + sparse vectors |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j Bolt endpoint |
| `NEO4J_USER` / `NEO4J_PASSWORD` | `neo4j` / `codegraphix` | Neo4j credentials |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins |
| `VECTOR_BACKEND` | `auto` | `auto` (probe + fallback) · `memory` · `qdrant` |
| `GRAPH_BACKEND` | `auto` | `auto` (probe + fallback) · `memory` · `neo4j` |
| `EMBEDDING_PROVIDER` | `hashing` | `hashing` (deterministic, offline) · `openai` |
| `EMBEDDING_DIMENSIONS` | `256` | Dense vector width for the hashing provider |
| `OPENAI_API_KEY` | *(empty)* | Required for `openai` embeddings |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible endpoint |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Dense model |
| `RRF_K` | `60` | Reciprocal Rank Fusion constant |
| `SEARCH_CANDIDATES` | `64` | Dense/sparse candidates before fusion |
| `DENSE_RELEVANCE_FLOOR` | `0.08` | Raw cosine floor; unrelated chunks are excluded |
| `GRAPH_MAX_HOPS` | `1` | Traversal depth budget |
| `GRAPH_MAX_SYMBOLS` | `24` | Max symbols injected into the answer context |
| `CHUNK_MAX_CHARS` | `1600` | Per-chunk embedding budget; larger declarations split |
| `INGEST_MAX_FILES` | `2000` | Files scanned per ingestion |
| `INGEST_MAX_FILE_BYTES` | `524288` | Per-file size cap (512 KiB) |
| `IGNORE_DIRS` | *(list)* | Directory names excluded from indexing |
| `SEED_TENANTS` | `acme,globex` | Tenants seeded with the demo corpus at startup |
| `LLM_API_KEY` | *(empty)* | Enables LLM answer synthesis |
| `LLM_BASE_URL` / `LLM_MODEL` | `…/v1` / `gpt-4o-mini` | Chat completion endpoint |

---

## API Reference

### `GET /health`

Service status, active backend selection, and embedding provider.

```json
{
  "status": "ok",
  "services": {
    "api": "ready",
    "vector": "memory",
    "graph": "memory",
    "qdrant_fallback": "unreachable (in-memory engine active)",
    "neo4j_fallback": "unreachable (in-memory engine active)"
  },
  "embedding": "hashing-v1",
  "version": "1.0.0"
}
```

### `POST /v1/ingest`

Index a repository. `mode` selects the source; `wait=true` blocks for the
result, `wait=false` returns a job id to poll.

```json
{
  "tenant_id": "acme",
  "repository_id": "atlas-api",
  "mode": "sample"
}
```

```json
{
  "status": "indexed",
  "tenant_id": "acme",
  "repository_id": "atlas-api",
  "commit_sha": "d4c81b25f0aa",
  "mode": "sample",
  "message": "Indexed 8 files -> 25 symbols, 25 chunks, 41 edges.",
  "stats": {
    "files_scanned": 8, "files_indexed": 8, "files_skipped": 0,
    "symbols_indexed": 25, "chunks_indexed": 25, "edges_indexed": 41,
    "warnings": []
  },
  "duration_ms": 187.9
}
```

Local and git modes:

```json
{ "tenant_id": "acme", "repository_id": "my-service", "mode": "local", "path": "C:\\code\\my-service" }
{ "tenant_id": "acme", "repository_id": "open-source-x", "mode": "git", "git_url": "https://github.com/org/repo.git", "branch": "main" }
```

### `GET /v1/ingest/{job_id}`

Poll an asynchronous ingestion job (`status`: `running` | `done` | `failed`).

### `POST /v1/search`

Hybrid search with graph expansion and answers.

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

Response highlights: `results[]` (ranked chunks with symbol, file, language,
kind, repo, hybrid/dense/sparse scores, line range, snippet, callers,
callees, imports, doc comment), `context_symbols[]` (the symbols injected
into the prompt), `graph_edges[]` (resolved relationships), `answer`
(cited), `citations[]`, `latency_ms`, and `mode` (e.g.
`hybrid-rrf+memory+graph+answer`).

### `GET /v1/repositories?tenant_id=acme`

Indexed repositories for a tenant, with commit SHA and counts.

### `DELETE /v1/repositories/{tenant_id}/{repository_id}`

Remove a repository and all of its chunks, vectors, docs, and edges.

### `GET /v1/stats?tenant_id=acme`

Per-tenant statistics: repositories, files, symbols, chunks, edges, and
detected languages.

### `GET /v1/graph?tenant_id=acme&repository_id=atlas-api&limit=64`

Symbol graph snapshot (nodes + edges) for visualization.

---
## Retrieval Pipeline Deep Dive

### Embeddings

The default **hashing embedder** is deterministic, offline, and dependency
free:

- **Code-aware tokens**: the tokenizer emits the lowercased identifier *and*
  its camelCase / snake_case sub-tokens, so `getUserData` produces
  `getuserdata`, `user`, and `data`.
- **Dense vector** (256-dim, L2-normalized): tokens and character trigrams
  are hashed (via BLAKE2b) into buckets with signed weights scaled by term
  frequency. Trigrams soften the collision damage of bag-of-tokens hashing
  and make short identifiers searchable.
- **Sparse vector**: the same tokens are hashed into a large prime bucket
  space with BM25-style weights, matching Qdrant's `Modifier.IDF` sparse
  vector semantics.
- Set `EMBEDDING_PROVIDER=openai` to swap the dense half for
  `text-embedding-3-small` (or any compatible endpoint); sparse weights stay
  local. Any failure degrades to hashing.

### Hybrid scoring with Reciprocal Rank Fusion

Both stores support an identical search interface, so the retrieval code is
backend-agnostic:

| Stage | Operation |
| ----- | --------- |
| Dense | Cosine between the query embedding and every tenant-scoped chunk |
| Sparse | BM25 over tokenized chunks with precomputed query IDFs |
| Fusion | RRF: `score(id) = Σ 1 / (k + rank)` over both rankings |
| Floor | Require a sparse hit or a raw dense cosine ≥ `DENSE_RELEVANCE_FLOOR` |
| Hydration | Resolve chunk ids → documents → full `SearchResult` objects with callers/callees |

### Graph expansion

For the top matches the engine traverses the symbol graph bidirectionally:

```text
PaymentService.charge ──CALLS──▶ StripeGateway.capture
                      ──CALLS──▶ CustomerRepository.findByUser
        ◀──CALLS──── CheckoutController.create
```

Edge targets extracted from source are frequently shorthand
(`gateway.capture` instead of `StripeGateway.capture`). The graph store
resolves targets against indexed symbols by exact name, then suffix, then
final identifier segment, so the graph stays connected without inventing
relationships. Traversal is hard-limited by `GRAPH_MAX_HOPS` and
`GRAPH_MAX_SYMBOLS`.

### Answers

- **Extractive (always on)**: the strongest match becomes the anchor, related
  matches and callers/callees are named, and every claim maps to a chunk
  with a file path and line range.
- **LLM (optional)**: with `LLM_API_KEY` set, the assembled context is sent
  to a chat-completions endpoint with a strict grounded-answer prompt. Any
  failure — network, quota, timeout — falls back silently to the extractive
  answer.

---

## Chunking Design

The chunker is in `backend/app/chunking.py`:

- **Primary engine**: tree-sitter grammars for TypeScript, TSX, JavaScript,
  Python, Go, Java, and Rust. Declarations are found by node type
  (`function_declaration`, `class_declaration`, `method_definition`,
  `struct_item`, `impl_item`, …), converted to chunks with qualified names,
  exact byte spans, line ranges, doc comments, and extracted
  relationships.
- **Fallbacks**: when tree-sitter is unavailable, Python uses the standard
  library `ast`; other brace languages fall back to a deterministic
  line scanner that respects brace balance.
- **Dynamic splitting**: chunks over `CHUNK_MAX_CHARS` are split into
  overlap-free line-bounded parts; `part_index` / `part_count` describe the
  split and the parent symbol is preserved.
- **Module chunks**: top-level executable code outside any declaration
  becomes a `<module>` chunk so scripts and glue code remain searchable.
- **Relationship extraction**: a language-neutral call detector
  (`/\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(/`) plus import
  parsers per language feed the CALLS / IMPORTS / EXTENDS / IMPLEMENTS
  edges.

---

## Storage Design

### Qdrant point payload

Each chunk becomes one point in a collection configured with a **dense**
named vector and a **sparse** vector.

```json
{
  "id": "stable-symbol-or-content-id",
  "dense": [0.01, -0.02, 0.34],
  "sparse": { "indices": [12, 42], "values": [0.8, 0.4] },
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
    "doc": "Handles payment authorization …",
    "tokens": ["payment", "service", "charge", "…"]
  }
}
```

A dense vector captures semantic similarity; a sparse vector rewards exact
symbol names, framework methods, identifiers, and error codes. Reciprocal
rank fusion combines both rankings with no weight tuning.

### Neo4j graph model

```cypher
(:Tenant)-[:OWNS]->(:Repository)
(:Repository)-[:CONTAINS]->(:File)
(:File)-[:DECLARES]->(:Symbol)
(:Symbol)-[:CALLS]->(:Symbol)
(:Symbol)-[:IMPORTS]->(:Symbol)
(:Symbol)-[:EXTENDS|IMPLEMENTS]->(:Symbol)
```

Symbol nodes are keyed by `tenant_id + repository_id + name` so re-indexing
a revision is idempotent. All traversals enforce a maximum depth and a
result budget.

---

## Tenancy Model

Every durable record is scoped by `tenant_id`, enforced in three places:

1. **Vector filtering**: every Qdrant search includes a mandatory tenant
   filter; the in-memory store scopes candidate selection the same way.
2. **Graph patterns**: Neo4j lookups carry tenant equality in node patterns;
   the in-memory graph keys edges by tenant.
3. **Repository registry and stats**: all reads are tenant-scoped.

`repository_id` values are unique within a tenant. A useful symbol identity
is `(tenant_id, repository_id, commit_sha, fully_qualified_name, start_byte)`,
which makes re-indexing a commit idempotent and keeps historical revisions
available for audit or branch-aware queries.

A production deployment should additionally derive the tenant from the
signed identity (OIDC/SSO claims) instead of trusting a user-supplied
`tenant_id` for authorization.

---
## Testing Strategy

The repository ships with **46 tests** and they run without any external
service:

```powershell
python -m pytest backend/tests -q
```

| Test file | Coverage |
| --------- | -------- |
| `test_chunking.py` | AST chunk boundaries, qualified names, line/byte ranges, doc comments, calls/imports extraction, Python `ast` fallback, class bases → EXTENDS edges, idempotent chunk ids, dynamic splitting of large declarations, unsupported-language safety |
| `test_embeddings.py` | CamelCase tokenization, deterministic hashing, similarity ordering (similar > unrelated), sparse-vector structure, L2 normalization, BM25 preference |
| `test_retrieval_api.py` | Demo seeding, payment-query ranking, RRF descending order, repository filters, relevance floor, **tenant isolation** (globex never sees acme chunks), graph shorthand resolution, expansion budgets, idempotent re-ingest, local-path ingestion, git failure handling, and the full HTTP contract (health, search compatibility, stats, repositories, graph model, async jobs, validation) |

The recommended evaluation set asks questions **whose answer is not present
in the highest-ranked file alone**, e.g.:

- Where a value is transformed before persistence.
- Which controller triggers a worker.
- What implementation satisfies an interface.
- Which configuration value changes a provider's behavior.

---

## Production Hardening Checklist

- Add OIDC or SSO authentication and derive tenant membership from claims.
- Store secrets in a managed secret store; never commit `.env` files.
- Use separate Qdrant collections *or* strict payload filters per tenant.
- Add Neo4j constraints for tenant, repository, commit, and symbol identities.
- Run ingestion in a queue with retry, cancellation, and progress events.
- Persist parser errors and unsupported file types as inspectable job warnings.
- Add an embedding cache keyed by content hash and embedding model version.
- Redact secrets and credentials before embedding source text.
- Add answer citations with repository, commit, file path, and line range.
- Apply graph traversal budgets, query timeouts, and prompt token limits.
- Add structured logs, metrics, tracing, rate limits, and audit events.
- Pin grammars and model versions to make indexing reproducible.

---

## Repository Layout

```text
CodeGraphix/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py           # FastAPI routes + lifespan startup
│   │   ├── config.py         # environment-driven settings
│   │   ├── models.py         # request / response contracts
│   │   ├── engine.py         # composition root, registry, jobs, seeding
│   │   ├── chunking.py       # tree-sitter + fallback AST chunkers
│   │   ├── embeddings.py     # hashing / openai embedders, BM25
│   │   ├── stores.py         # memory & Qdrant vector stores; memory & Neo4j graphs
│   │   ├── retrieval.py      # hybrid search, RRF, graph expansion
│   │   ├── answering.py      # cited extractive + optional LLM answers
│   │   ├── ingest.py         # sample / local / git ingestion pipeline
│   │   └── demo_corpus.py    # two-tenant demo repositories
│   ├── tests/                # 46 pytest cases (chunking, retrieval, API)
│   ├── Dockerfile
│   ├── README.md
│   └── requirements.txt
├── src/app/
│   ├── globals.css           # visual system and responsive layout
│   ├── layout.tsx            # metadata and root layout
│   └── page.tsx              # interactive code-intelligence workspace
├── docker-compose.yml        # web + api + qdrant + neo4j (persistent volumes)
├── Dockerfile                # multi-stage Next.js standalone build
├── .env.example              # full configuration reference
├── .dockerignore
└── README.md
```

---

## Roadmap

- [x] Deterministic offline demo with two tenant corpora
- [x] Tree-sitter AST chunking across 7 language families
- [x] Hybrid retrieval (dense + BM25) with RRF fusion
- [x] Bounded graph expansion with shorthand edge resolution
- [x] Cited extractive answers + optional LLM synthesis
- [x] Qdrant & Neo4j adapters with in-memory fallback
- [x] Async ingestion jobs with polling
- [x] Automated test suite (46 tests)
- [ ] Streaming LLM answers over SSE
- [ ] GitHub App / webhook-driven re-indexing
- [ ] Incremental indexing (only changed files since a commit)
- [ ] Clickhouse-scale telemetry on query latency by tenant
- [ ] Symbol-level code navigation (definition / reference jumping)


## License

CodeGraphix is released under the **MIT License**.

MIT is the best fit for this project for three reasons:

1. **Zero-friction adoption.** This is a code-intelligence platform teams are meant to build on, extend, and fork. MIT imposes no copyleft obligations, so downstream users may ship proprietary internal or commercial versions without being forced to open-source their changes. Under a copyleft license like GPL/AGPL, embedding your modifications into a closed codebase would require releasing those changes, which is exactly the friction that slows adoption for developer tooling.

2. **Stack alignment.** CodeGraphix sits on top of permissive, MIT-adjacent ecosystems — Next.js, React, FastAPI, and tree-sitter grammars. Choosing MIT matches the licensing posture of the libraries it depends on and keeps the project legally self-consistent.

3. **Attribution is the only requirement.** MIT asks you to preserve copyright/license notice in copies and derivatives, which is trivial and appropriate for open-source infrastructure. That's preferable to a permissive-only *public-domain*-style dedication in this case: real projects have real maintainers and contributors, and MIT's notice requirement is a light-touch way to keep authorship visible.

If you need the opposite posture — for example, a self-contained internal tool where you do not care about downstream licensing at all, or a truly zero-obligation public-domain release — consider the **[Unlicense](https://unlicense.org/)** or **[CC0](https://creativecommons.org/publicdomain/zero/1.0/)**. If you require that improvements to this project stay open-sourced, prefer **GPL-3.0** or **AGPL-3.0** instead. For this project as described, MIT is the intended and recommended choice.

```text
MIT License

Copyright (c) 2024-2026 CodeGraphix contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---
---

