"""Storage layer: document store, vector stores, and graph stores.

The in-memory implementations are the deterministic default: they need no
services, produce identical results across restarts, and are fast enough for
demo-scale corpora. The Qdrant and Neo4j adapters implement the same
interfaces and are activated automatically when the services are reachable
(or pinned via configuration), so production deployments scale without
changing the API contract.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from .chunking import Chunk


# --- records ---------------------------------------------------------------------


@dataclass
class VectorRecord:
    """One point in the vector store: dense + sparse vectors plus payload."""

    id: str
    tenant_id: str
    repository_id: str
    commit_sha: str
    dense: list[float]
    sparse_indices: list[int]
    sparse_values: list[float]
    payload: dict[str, Any]


@dataclass
class NodeRecord:
    """A symbol node in the dependency graph."""

    name: str
    kind: str
    file_path: str
    repository_id: str
    start_line: int
    end_line: int


@dataclass
class EdgeRecord:
    """A directed relationship between symbol names."""

    source: str
    relation: str
    target: str
    resolved: bool = True


RELATION_TYPES = ("CALLS", "IMPORTS", "EXTENDS", "IMPLEMENTS")


# --- document store ----------------------------------------------------------------


class MemoryDocStore:
    """Tenant-scoped source of truth for chunk contents and metadata."""

    def __init__(self) -> None:
        self._chunks: dict[str, Chunk] = {}

    def put(self, chunks: Iterable[Chunk]) -> int:
        count = 0
        for chunk in chunks:
            self._chunks[chunk.id] = chunk
            count += 1
        return count

    def get(self, chunk_id: str) -> Chunk | None:
        return self._chunks.get(chunk_id)

    def get_many(self, chunk_ids: Iterable[str]) -> list[Chunk]:
        found: list[Chunk] = []
        for chunk_id in chunk_ids:
            chunk = self._chunks.get(chunk_id)
            if chunk is not None:
                found.append(chunk)
        return found

    def iter_scope(self, tenant_id: str, repository_id: str | None = None) -> list[Chunk]:
        return [
            chunk
            for chunk in self._chunks.values()
            if chunk.tenant_id == tenant_id and (repository_id is None or chunk.repository_id == repository_id)
        ]

    def delete_repository(self, tenant_id: str, repository_id: str) -> int:
        doomed = [key for key, chunk in self._chunks.items() if chunk.tenant_id == tenant_id and chunk.repository_id == repository_id]
        for key in doomed:
            del self._chunks[key]
        return len(doomed)

    def count(self, tenant_id: str | None = None, repository_id: str | None = None) -> int:
        if tenant_id is None:
            return len(self._chunks)
        return len(self.iter_scope(tenant_id, repository_id))

class MemoryVectorStore:
    """Brute-force vector store with cosine dense search and BM25 sparse search."""

    backend = "memory"

    def __init__(self) -> None:
        self._points: dict[str, VectorRecord] = {}

    # -- writes -----------------------------------------------------------------

    def upsert(self, records: Iterable[VectorRecord]) -> int:
        count = 0
        for record in records:
            self._points[record.id] = record
            count += 1
        return count

    def delete_repository(self, tenant_id: str, repository_id: str) -> int:
        doomed = [key for key, record in self._points.items() if record.tenant_id == tenant_id and record.repository_id == repository_id]
        for key in doomed:
            del self._points[key]
        return len(doomed)

    # -- reads ------------------------------------------------------------------

    def _scoped(self, tenant_id: str, repository_id: str | None, language: str | None, kind: str | None) -> list[VectorRecord]:
        return [
            record
            for record in self._points.values()
            if record.tenant_id == tenant_id
            and (repository_id is None or record.repository_id == repository_id)
            and (language is None or record.payload.get("language") == language)
            and (kind is None or record.payload.get("kind") == kind)
        ]

    def search_dense(
        self,
        tenant_id: str,
        dense: list[float],
        repository_id: str | None = None,
        language: str | None = None,
        kind: str | None = None,
        limit: int = 32,
    ) -> list[tuple[str, float]]:
        scoped = self._scoped(tenant_id, repository_id, language, kind)
        scored: list[tuple[str, float]] = []
        for record in scoped:
            dot = 0.0
            for left, right in zip(dense, record.dense):
                dot += left * right
            scored.append((record.id, dot))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def search_sparse(
        self,
        tenant_id: str,
        tokens: list[str],
        repository_id: str | None = None,
        language: str | None = None,
        kind: str | None = None,
        limit: int = 32,
    ) -> list[tuple[str, float]]:
        scoped = self._scoped(tenant_id, repository_id, language, kind)
        if not scoped or not tokens:
            return []
        document_frequency: dict[str, int] = defaultdict(int)
        tokenized: dict[str, dict[str, int]] = {}
        for record in scoped:
            counts: dict[str, int] = defaultdict(int)
            for token in record.payload.get("tokens", []):
                counts[token] += 1
            tokenized[record.id] = dict(counts)
            for token in counts:
                document_frequency[token] += 1
        total = len(scoped)
        average_length = sum(len(tokens_map) for tokens_map in tokenized.values()) / total
        idf = {
            token: math.log(1.0 + (total - df + 0.5) / (df + 0.5))
            for token, df in document_frequency.items()
        }
        scored: list[tuple[str, float]] = []
        for record in scoped:
            counts = tokenized[record.id]
            score = 0.0
            for token in set(tokens):
                tf = counts.get(token, 0)
                if tf == 0:
                    continue
                numerator = tf * 2.5  # k1 + 1
                denominator = tf + 1.5 * (1.0 - 0.75 + 0.75 * (len(counts) / (average_length or 1.0)))
                score += idf.get(token, 0.0) * (numerator / denominator)
            if score > 0:
                scored.append((record.id, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def count(self, tenant_id: str | None = None, repository_id: str | None = None) -> int:
        if tenant_id is None:
            return len(self._points)
        return len(self._scoped(tenant_id, repository_id, None, None))

    def languages(self, tenant_id: str) -> list[str]:
        found = {record.payload.get("language", "") for record in self._scoped(tenant_id, None, None, None)}
        return sorted(found - {""})

class MemoryGraphStore:
    """In-memory symbol graph with bounded traversal and name resolution.

    Edge targets extracted from source are frequently shorthand (``gateway.capture``
    instead of ``StripeGateway.capture``). ``resolve`` matches targets against
    indexed symbols by exact name, then suffix, then final identifier segment
    so the graph stays connected without inventing relationships.
    """

    backend = "memory"

    def __init__(self) -> None:
        self._nodes: dict[tuple[str, str, str], NodeRecord] = {}
        self._edges: dict[tuple[str, str], list[EdgeRecord]] = {}

    # -- writes -----------------------------------------------------------------

    def upsert_symbols(self, tenant_id: str, repository_id: str, nodes: list[NodeRecord]) -> int:
        for node in nodes:
            self._nodes[(tenant_id, repository_id, node.name)] = node
        return len(nodes)

    def upsert_edges(self, tenant_id: str, repository_id: str, edges: list[EdgeRecord]) -> int:
        key = (tenant_id, repository_id)
        existing = self._edges.setdefault(key, [])
        seen = {(edge.source, edge.relation, edge.target) for edge in existing}
        added = 0
        for edge in edges:
            resolved = self.resolve(tenant_id, repository_id, edge.target)
            canonical = resolved or edge.target
            record = EdgeRecord(edge.source, edge.relation, canonical, resolved is not None)
            marker = (record.source, record.relation, record.target)
            if marker in seen:
                continue
            seen.add(marker)
            existing.append(record)
            added += 1
        return added

    def resolve(self, tenant_id: str, repository_id: str, target: str) -> str | None:
        """Resolve a raw call/extends name to an indexed symbol name."""
        direct = self._nodes.get((tenant_id, repository_id, target))
        if direct is not None:
            return direct.name
        candidates = [
            name
            for (tenant, repo, name) in self._nodes
            if tenant == tenant_id and repo == repository_id
        ]
        for name in candidates:
            if name.endswith("." + target):
                return name
        last = target.rsplit(".", 1)[-1]
        exact_last = sorted(name for name in candidates if name.rsplit(".", 1)[-1] == last)
        if exact_last:
            return exact_last[0]
        if "." in target:
            for name in sorted(candidates):
                if name.endswith(target):
                    return name
        return None

    def delete_repository(self, tenant_id: str, repository_id: str) -> None:
        for key in [key for key in self._nodes if key[0] == tenant_id and key[1] == repository_id]:
            del self._nodes[key]
        self._edges.pop((tenant_id, repository_id), None)

    # -- reads ------------------------------------------------------------------

    def neighbors(
        self,
        tenant_id: str,
        symbol: str,
        repository_id: str | None = None,
        relations: tuple[str, ...] = ("CALLS", "EXTENDS", "IMPLEMENTS"),
        hops: int = 1,
        limit: int = 24,
    ) -> tuple[dict[str, NodeRecord], list[EdgeRecord]]:
        """Bounded bidirectional traversal from ``symbol``."""
        frontier = [symbol]
        visited: set[str] = {symbol}
        nodes: dict[str, NodeRecord] = {}
        edges: list[EdgeRecord] = []
        for _ in range(max(1, hops)):
            next_frontier: list[str] = []
            for current in frontier:
                for edge in self._edges_for(tenant_id, repository_id, current, relations):
                    other = edge.target if edge.source == current else edge.source
                    if edge not in edges:
                        edges.append(edge)
                    if other in visited:
                        continue
                    visited.add(other)
                    record = self._lookup(tenant_id, other)
                    if record is not None:
                        nodes[other] = record
                        next_frontier.append(other)
                    if len(visited) >= limit + 1:
                        return nodes, edges
            frontier = next_frontier
        return nodes, edges

    def _edges_for(self, tenant_id: str, repository_id: str | None, symbol: str, relations: tuple[str, ...]) -> list[EdgeRecord]:
        result: list[EdgeRecord] = []
        for (tenant, repo), edge_list in self._edges.items():
            if tenant != tenant_id:
                continue
            if repository_id is not None and repo != repository_id:
                continue
            for edge in edge_list:
                if edge.relation not in relations:
                    continue
                if edge.source == symbol or edge.target == symbol:
                    result.append(edge)
        return result

    def _lookup(self, tenant_id: str, name: str) -> NodeRecord | None:
        for (tenant, _repo, node_name), record in self._nodes.items():
            if tenant == tenant_id and node_name == name:
                return record
        return None

    def stats(self, tenant_id: str, repository_id: str | None = None) -> tuple[int, int]:
        symbols = sum(
            1
            for key in self._nodes
            if key[0] == tenant_id and (repository_id is None or key[1] == repository_id)
        )
        edges = sum(
            1
            for (tenant, repo), edge_list in self._edges.items()
            if tenant == tenant_id and (repository_id is None or repo == repository_id)
            for _ in edge_list
        )
        return symbols, edges

    def snapshot(self, tenant_id: str, repository_id: str | None = None, limit: int = 64) -> tuple[list[NodeRecord], list[EdgeRecord]]:
        nodes = [
            record
            for (tenant, repo, _name), record in self._nodes.items()
            if tenant == tenant_id and (repository_id is None or repo == repository_id)
        ][:limit]
        node_names = {node.name for node in nodes}
        edges = [
            edge
            for (tenant, repo), edge_list in self._edges.items()
            if tenant == tenant_id and (repository_id is None or repo == repository_id)
            for edge in edge_list
            if edge.source in node_names and edge.target in node_names
        ][: limit * 2]
        return nodes, edges

class QdrantVectorStore:
    """Qdrant-backed vector store using named dense and sparse vectors.

    Activated when ``VECTOR_BACKEND=auto`` detects a reachable Qdrant, or
    pinned with ``VECTOR_BACKEND=qdrant``. Falls back to the in-memory store
    via the engine when the service cannot be reached.
    """

    backend = "qdrant"

    def __init__(self, url: str, collection: str, dimensions: int) -> None:
        from qdrant_client import QdrantClient, models

        self._models = models
        self._client = QdrantClient(url=url, timeout=3)
        self._collection = collection
        self._dimensions = dimensions
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        from qdrant_client import models

        existing = [item.name for item in self._client.get_collections().collections]
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config={
                    "dense": models.VectorParams(size=self._dimensions, distance=models.Distance.COSINE),
                },
                sparse_vectors_config={
                    "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF),
                },
            )

    def upsert(self, records: Iterable[VectorRecord]) -> int:
        from qdrant_client import models

        points = [
            models.PointStruct(
                id=int(record.id[:15], 16) % (2**63 - 1),
                vector={
                    "dense": record.dense,
                    "sparse": models.SparseVector(indices=record.sparse_indices, values=record.sparse_values),
                },
                payload={**record.payload, "chunk_id": record.id},
            )
            for record in records
        ]
        if points:
            self._client.upsert(collection_name=self._collection, points=points, wait=True)
        return len(points)

    def _filter(self, tenant_id: str, repository_id: str | None, language: str | None, kind: str | None):
        from qdrant_client import models

        conditions = [models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id))]
        if repository_id:
            conditions.append(models.FieldCondition(key="repository_id", match=models.MatchValue(value=repository_id)))
        if language:
            conditions.append(models.FieldCondition(key="language", match=models.MatchValue(value=language)))
        if kind:
            conditions.append(models.FieldCondition(key="kind", match=models.MatchValue(value=kind)))
        return models.Filter(must=conditions)

    def search_dense(
        self,
        tenant_id: str,
        dense: list[float],
        repository_id: str | None = None,
        language: str | None = None,
        kind: str | None = None,
        limit: int = 32,
    ) -> list[tuple[str, float]]:
        response = self._client.query_points(
            collection_name=self._collection,
            using="dense",
            query=dense,
            query_filter=self._filter(tenant_id, repository_id, language, kind),
            limit=limit,
            with_payload=True,
        )
        return [(point.payload.get("chunk_id", ""), point.score) for point in response.points]

    def search_sparse(
        self,
        tenant_id: str,
        tokens: list[str],
        repository_id: str | None = None,
        language: str | None = None,
        kind: str | None = None,
        limit: int = 32,
    ) -> list[tuple[str, float]]:
        from qdrant_client import models

        indices, values = sparse_query_vector(tokens)
        response = self._client.query_points(
            collection_name=self._collection,
            using="sparse",
            query=models.SparseVector(indices=indices, values=values),
            query_filter=self._filter(tenant_id, repository_id, language, kind),
            limit=limit,
            with_payload=True,
        )
        return [(point.payload.get("chunk_id", ""), point.score) for point in response.points]

    def delete_repository(self, tenant_id: str, repository_id: str) -> int:
        from qdrant_client import models

        result = self._client.delete(
            collection_name=self._collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
                        models.FieldCondition(key="repository_id", match=models.MatchValue(value=repository_id)),
                    ]
                )
            ),
            wait=True,
        )
        return int(getattr(result, "operated", 0) or 0)

    def count(self, tenant_id: str | None = None, repository_id: str | None = None) -> int:
        if tenant_id is None:
            return self._client.count(collection_name=self._collection, exact=True).count
        return self._client.count(
            collection_name=self._collection,
            count_filter=self._filter(tenant_id, repository_id, None, None),
            exact=True,
        ).count

    def languages(self, tenant_id: str) -> list[str]:
        return []


_SPARSE_SPACE = 1_000_003


def sparse_query_vector(tokens: list[str]) -> tuple[list[int], list[float]]:
    """Hash tokens into the sparse bucket space for query-side sparse vectors."""
    import hashlib

    counts: dict[int, float] = {}
    for token, tf in Counter(tokens).items():
        digest = hashlib.blake2b(f"7:{token}".encode(), digest_size=8).digest()
        bucket = int.from_bytes(digest, "big") % _SPARSE_SPACE
        counts[bucket] = counts.get(bucket, 0.0) + 1.0 + math.log(tf)
    indices = sorted(counts)
    return indices, [counts[index] for index in indices]

class Neo4jGraphStore:
    """Neo4j-backed symbol graph using the ``Symbol`` label model.

    Relationship types (CALLS, EXTENDS, IMPLEMENTS, IMPORTS) are created
    statically per whitelist because Cypher does not parameterize types.
    Activated when ``GRAPH_BACKEND=auto`` detects a reachable Neo4j.
    """

    backend = "neo4j"

    def __init__(self, uri: str, user: str, password: str) -> None:
        from neo4j import GraphDatabase

        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._driver.verify_connectivity()

    def close(self) -> None:
        self._driver.close()

    def _run(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._driver.session() as session:
            return [record.data() for record in session.run(query, parameters or {})]

    def upsert_symbols(self, tenant_id: str, repository_id: str, nodes: list[NodeRecord]) -> int:
        query = (
            "UNWIND $rows AS row "
            "MERGE (s:Symbol {tenant_id: $tenant, repository_id: $repo, name: row.name}) "
            "SET s.kind = row.kind, s.file_path = row.file_path, "
            "s.start_line = row.start_line, s.end_line = row.end_line"
        )
        rows = [
            {"name": node.name, "kind": node.kind, "file_path": node.file_path, "start_line": node.start_line, "end_line": node.end_line}
            for node in nodes
        ]
        if rows:
            self._run(query, {"rows": rows, "tenant": tenant_id, "repo": repository_id})
        return len(rows)

    def upsert_edges(self, tenant_id: str, repository_id: str, edges: list[EdgeRecord]) -> int:
        added = 0
        for relation in RELATION_TYPES:
            rows = [{"source": edge.source, "target": edge.target} for edge in edges if edge.relation == relation]
            if not rows:
                continue
            query = (
                f"UNWIND $rows AS row "
                f"MERGE (a:Symbol {{tenant_id: $tenant, repository_id: $repo, name: row.source}}) "
                f"MERGE (b:Symbol {{tenant_id: $tenant, repository_id: $repo, name: row.target}}) "
                f"MERGE (a)-[e:{relation}]->(b)"
            )
            self._run(query, {"rows": rows, "tenant": tenant_id, "repo": repository_id})
            added += len(rows)
        return added

    def neighbors(
        self,
        tenant_id: str,
        symbol: str,
        repository_id: str | None = None,
        relations: tuple[str, ...] = ("CALLS", "EXTENDS", "IMPLEMENTS"),
        hops: int = 1,
        limit: int = 24,
    ) -> tuple[dict[str, NodeRecord], list[EdgeRecord]]:
        depth = max(1, min(hops, 3))
        rels = "|".join(relations)
        query = (
            f"MATCH (s:Symbol {{tenant_id: $tenant}})-[{rels}*1..{depth}]-(o:Symbol {{tenant_id: $tenant}}) "
            f"WHERE s.name = $name "
            f"RETURN DISTINCT o.name AS name, o.kind AS kind, o.file_path AS file_path, "
            f"o.start_line AS start_line, o.end_line AS end_line LIMIT $limit"
        )
        rows = self._run(query, {"tenant": tenant_id, "name": symbol, "limit": limit})
        nodes = {
            row["name"]: NodeRecord(
                name=row["name"],
                kind=row.get("kind") or "symbol",
                file_path=row.get("file_path") or "",
                repository_id=repository_id or "",
                start_line=row.get("start_line") or 0,
                end_line=row.get("end_line") or 0,
            )
            for row in rows
        }
        names = [symbol, *nodes]
        edge_query = (
            f"MATCH (a:Symbol {{tenant_id: $tenant}})-[r:{rels}]-(b:Symbol {{tenant_id: $tenant}}) "
            f"WHERE a.name IN $names AND b.name IN $names "
            f"RETURN a.name AS source, type(r) AS relation, b.name AS target LIMIT $limit"
        )
        edge_rows = self._run(edge_query, {"tenant": tenant_id, "names": names, "limit": limit * 2})
        edges = [EdgeRecord(row["source"], row["relation"], row["target"], True) for row in edge_rows]
        return nodes, edges

    def delete_repository(self, tenant_id: str, repository_id: str) -> None:
        self._run(
            "MATCH (n:Symbol {tenant_id: $tenant, repository_id: $repo}) DETACH DELETE n",
            {"tenant": tenant_id, "repo": repository_id},
        )

    def stats(self, tenant_id: str, repository_id: str | None = None) -> tuple[int, int]:
        symbols = self._run("MATCH (s:Symbol {tenant_id: $tenant}) RETURN count(s) AS c", {"tenant": tenant_id})
        edges = self._run(
            "MATCH (a:Symbol {tenant_id: $tenant})-[r]->(b:Symbol {tenant_id: $tenant}) RETURN count(r) AS c",
            {"tenant": tenant_id},
        )
        return (int(symbols[0]["c"]) if symbols else 0), (int(edges[0]["c"]) if edges else 0)

    def snapshot(self, tenant_id: str, repository_id: str | None = None, limit: int = 64) -> tuple[list[NodeRecord], list[EdgeRecord]]:
        node_rows = self._run(
            "MATCH (s:Symbol {tenant_id: $tenant}) RETURN s.name AS name, s.kind AS kind, "
            "s.file_path AS file_path, s.start_line AS start_line, s.end_line AS end_line LIMIT $limit",
            {"tenant": tenant_id, "limit": limit},
        )
        nodes = [
            NodeRecord(
                name=row["name"],
                kind=row.get("kind") or "symbol",
                file_path=row.get("file_path") or "",
                repository_id=repository_id or "",
                start_line=row.get("start_line") or 0,
                end_line=row.get("end_line") or 0,
            )
            for row in node_rows
        ]
        names = [node.name for node in nodes]
        edge_rows = self._run(
            "MATCH (a:Symbol {tenant_id: $tenant})-[r]->(b:Symbol {tenant_id: $tenant}) "
            "WHERE a.name IN $names AND b.name IN $names "
            "RETURN a.name AS source, type(r) AS relation, b.name AS target LIMIT $limit",
            {"tenant": tenant_id, "names": names, "limit": limit * 2},
        )
        edges = [EdgeRecord(row["source"], row["relation"], row["target"], True) for row in edge_rows]
        return nodes, edges

def probe_qdrant(url: str, timeout: float = 2.0) -> bool:
    """Cheap TCP/HTTP probe for a Qdrant service."""
    try:
        import urllib.request

        with urllib.request.urlopen(f"{url.rstrip('/')}/readyz", timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception:  # noqa: BLE001 - any failure means "not available"
        return False


def probe_neo4j(uri: str, user: str, password: str, timeout: float = 2.0) -> bool:
    """Cheap socket probe for a Neo4j bolt endpoint."""
    try:
        import socket
        from urllib.parse import urlparse

        parsed = urlparse(uri.replace("bolt://", "http://", 1).replace("neo4j://", "http://", 1).replace("bolt+s://", "https://", 1))
        host = parsed.hostname or "localhost"
        port = parsed.port or 7687
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:  # noqa: BLE001
        return False


def build_vector_store(settings) -> Any:
    """Instantiate the configured vector backend, falling back to memory."""
    forced = settings.vector_backend
    if forced in ("memory", ""):
        return MemoryVectorStore()
    if forced == "qdrant" or (forced == "auto" and probe_qdrant(settings.qdrant_url)):
        try:
            return QdrantVectorStore(settings.qdrant_url, settings.qdrant_collection, settings.embedding_dimensions)
        except Exception:  # noqa: BLE001 - degrade to memory on any adapter error
            if forced == "qdrant":
                raise
    return MemoryVectorStore()


def build_graph_store(settings) -> Any:
    """Instantiate the configured graph backend, falling back to memory."""
    forced = settings.graph_backend
    if forced in ("memory", ""):
        return MemoryGraphStore()
    if forced == "neo4j" or (forced == "auto" and probe_neo4j(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)):
        try:
            return Neo4jGraphStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        except Exception:  # noqa: BLE001
            if forced == "neo4j":
                raise
    return MemoryGraphStore()








