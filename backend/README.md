# CodeGraphix API

FastAPI service for multi-tenant AST ingestion, hybrid retrieval, and dependency graph expansion. The current implementation includes a deterministic demo fallback so the UI and API can be exercised without credentials or running databases. Qdrant and Neo4j adapters can be added behind the service boundary without changing the HTTP contract.
