"""Hybrid retrieval, reciprocal rank fusion, and graph expansion.

Query path implemented here:

1. Embed the query into dense and sparse representations.
2. Run dense (cosine) and sparse (BM25) rankings inside the tenant scope.
3. Fuse both rankings with Reciprocal Rank Fusion.
4. Hydrate winning chunk ids from the document store.
5. Expand top matches through the dependency graph within strict budgets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Citation, GraphEdge, SearchResult
from .stores import EdgeRecord


@dataclass
class HybridResult:
    """One hydrated retrieval hit before API conversion."""

    chunk_id: str
    fused_score: float
    dense_score: float
    sparse_score: float
    rank: int


def rrf_fuse(
    dense_ranked: list[tuple[str, float]],
    sparse_ranked: list[tuple[str, float]],
    k: int = 60,
    candidates: int = 64,
) -> list[tuple[str, float, float, float]]:
    """Reciprocal Rank Fusion over two ranked id lists.

    Returns ``(id, fused, best_dense, best_sparse)`` sorted by fused score.
    """
    fused: dict[str, float] = {}
    dense_scores: dict[str, float] = {}
    sparse_scores: dict[str, float] = {}
    for rank, (chunk_id, score) in enumerate(dense_ranked[:candidates], start=1):
        fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (k + rank)
        dense_scores[chunk_id] = max(dense_scores.get(chunk_id, 0.0), score)
    for rank, (chunk_id, score) in enumerate(sparse_ranked[:candidates], start=1):
        fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (k + rank)
        sparse_scores[chunk_id] = max(sparse_scores.get(chunk_id, 0.0), score)
    ordered = sorted(fused.items(), key=lambda item: item[1], reverse=True)
    return [
        (chunk_id, score, dense_scores.get(chunk_id, 0.0), sparse_scores.get(chunk_id, 0.0))
        for chunk_id, score in ordered
    ]


def _normalize_scores(scored: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Scale scores to (0, 1] by dividing by the max, for display purposes."""
    if not scored:
        return []
    top = max(score for _id, score in scored) or 1.0
    return [(chunk_id, max(0.0, min(1.0, score / top))) for chunk_id, score in scored]


def hybrid_search(
    engine,
    *,
    tenant_id: str,
    query: str,
    limit: int = 8,
    repository_id: str | None = None,
    language: str | None = None,
    kind: str | None = None,
) -> list[HybridResult]:
    """Run the dense + sparse + RRF pipeline and hydrate results."""
    embedding = engine.embedder.embed(query)
    candidates = max(engine.settings.search_candidates, limit * 4)

    dense_ranked = engine.vector.search_dense(
        tenant_id, embedding.dense, repository_id, language, kind, candidates
    )
    sparse_ranked = engine.vector.search_sparse(
        tenant_id, embedding.tokens, repository_id, language, kind, candidates
    )

    dense_norm = _normalize_scores(dense_ranked)
    sparse_norm = _normalize_scores(sparse_ranked)
    dense_map = dict(dense_norm)
    sparse_map = dict(sparse_norm)
    raw_dense_map = dict(dense_ranked)

    # A chunk is only returned when it shows a genuine signal: it must appear
    # in the sparse (lexical/BM25) ranking, OR its raw dense cosine must clear
    # an absolute floor. Hashing embeddings produce a small positive cosine for
    # any text, so without this floor unrelated chunks leak in.
    sparse_ids = {chunk_id for chunk_id, _score in sparse_ranked}
    fused = rrf_fuse(dense_ranked, sparse_ranked, k=engine.settings.rrf_k, candidates=candidates)
    relevant = [
        entry
        for entry in fused
        if entry[0] in sparse_ids or raw_dense_map.get(entry[0], 0.0) >= engine.settings.dense_relevance_floor
    ]
    if not relevant and sparse_ids:
        relevant = fused[:2]

    results: list[HybridResult] = []
    for rank, (chunk_id, score, _dense_raw, _sparse_raw) in enumerate(relevant[:limit], start=1):
        results.append(
            HybridResult(
                chunk_id=chunk_id,
                fused_score=score,
                dense_score=round(dense_map.get(chunk_id, 0.0), 4),
                sparse_score=round(sparse_map.get(chunk_id, 0.0), 4),
                rank=rank,
            )
        )
    return results

def hydrate_results(engine, tenant_id: str, hybrid: list[HybridResult]) -> list[SearchResult]:
    """Convert fused chunk ids into API ``SearchResult`` objects."""
    chunks = engine.docs.get_many([item.chunk_id for item in hybrid])
    chunk_map = {chunk.id: chunk for chunk in chunks}

    results: list[SearchResult] = []
    for item in hybrid:
        chunk = chunk_map.get(item.chunk_id)
        if chunk is None:
            continue  # vector store and doc store disagree; skip silently
        callers, callees = _callers_and_callees(engine, tenant_id, chunk.symbol, chunk.repository_id)
        results.append(
            SearchResult(
                id=chunk.id,
                symbol=chunk.symbol,
                file_path=chunk.file_path,
                language=chunk.language,
                kind=chunk.kind,
                repository_id=chunk.repository_id,
                score=round(min(0.995, max(item.dense_score, item.sparse_score, 0.01)), 4),
                dense_score=item.dense_score,
                sparse_score=item.sparse_score,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                snippet=chunk.content[:1200],
                callers=callers,
                callees=callees,
                imports=chunk.imports,
                doc=chunk.doc,
            )
        )
    return results


def _callers_and_callees(engine, tenant_id: str, symbol: str, repository_id: str) -> tuple[list[str], list[str]]:
    """Direct callers / callees of one symbol from the graph store."""
    nodes, edges = engine.graph.neighbors(
        tenant_id,
        symbol,
        repository_id=repository_id,
        relations=("CALLS",),
        hops=1,
        limit=12,
    )
    callers: list[str] = []
    callees: list[str] = []
    for edge in edges:
        if edge.target == symbol and edge.source != symbol and edge.source not in callers:
            callers.append(edge.source)
        if edge.source == symbol and edge.target != symbol and edge.target not in callees:
            callees.append(edge.target)
    return callers[:6], callees[:6]


def expand_graph(
    engine,
    *,
    tenant_id: str,
    results: list[SearchResult],
    repository_id: str | None = None,
) -> tuple[list[str], list[GraphEdge]]:
    """Expand top matches through the graph within configured budgets.

    Returns the ordered context symbols (results first, then expansion) and
    the edges that connect them.
    """
    budget = engine.settings.graph_max_symbols
    hops = engine.settings.graph_max_hops

    context_symbols: list[str] = []
    graph_edges: list[GraphEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()

    for result in results[:5]:
        nodes, edges = engine.graph.neighbors(
            tenant_id,
            result.symbol,
            repository_id=repository_id,
            relations=("CALLS", "EXTENDS", "IMPLEMENTS"),
            hops=hops,
            limit=max(4, budget // 3),
        )
        for edge in edges:
            marker = (edge.source, edge.relation, edge.target)
            if marker in seen_edges:
                continue
            seen_edges.add(marker)
            graph_edges.append(GraphEdge(source=edge.source, relation=edge.relation, target=edge.target, resolved=edge.resolved))
        for name in nodes:
            if name not in context_symbols and len(context_symbols) < budget:
                context_symbols.append(name)

    # Results themselves lead the context, then expanded symbols fill the rest.
    merged = [result.symbol for result in results]
    merged += [name for name in context_symbols if name not in merged]
    return merged[:budget], graph_edges


