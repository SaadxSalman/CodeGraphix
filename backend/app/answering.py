"""Answer synthesis with citations.

Two layers:

1. Deterministic extractive layer (always on): turns the retrieved chunks,
   scores, and graph context into a compact, cited answer. Needs no external
   service and is fully reproducible, which makes it ideal for demos and
   contract tests.
2. Optional LLM layer: when ``LLM_API_KEY`` is configured, the assembled
   context is sent to an OpenAI-compatible chat completions endpoint with a
   grounded-answer system prompt. Any failure degrades silently to the
   extractive answer.
"""

from __future__ import annotations

from .models import Citation, SearchResult


SYSTEM_PROMPT = (
    "You are CodeGraphix, a code-intelligence assistant. Answer the user's "
    "question using ONLY the provided code context. Cite symbols with their "
    "file path and line range like `PaymentService.charge` "
    "(src/payments/service.ts:9-12). If the context is insufficient, say so. "
    "Be concise and concrete: name files, symbols, and relationships."
)


def build_context_blocks(results: list[SearchResult], context_symbols: list[str]) -> str:
    """Assemble the retrieval context for an LLM prompt."""
    blocks: list[str] = []
    for index, result in enumerate(results[:6], start=1):
        header = f"[{index}] {result.symbol} — {result.file_path} (lines {result.start_line}-{result.end_line}, {result.language} {result.kind}, repo {result.repository_id})"
        related = []
        if result.callers:
            related.append("callers: " + ", ".join(result.callers))
        if result.callees:
            related.append("callees: " + ", ".join(result.callees))
        if related:
            header += " | " + "; ".join(related)
        blocks.append(f"{header}\n```{result.language}\n{result.snippet}\n```")
    if context_symbols:
        extra = [symbol for symbol in context_symbols if symbol not in {r.symbol for r in results}]
        if extra:
            blocks.append("Graph-expanded context symbols: " + ", ".join(extra[:12]))
    return "\n\n".join(blocks)


def extractive_answer(
    query: str,
    results: list[SearchResult],
    context_symbols: list[str],
    vector_backend: str,
    graph_backend: str,
) -> str:
    """Deterministic, cited answer built purely from retrieved context."""
    if not results:
        return (
            "No matching code was found for this question in the indexed "
            "repositories for this tenant. Try a different query, or ingest "
            "the repository that should contain the answer."
        )

    top = results[0]
    lines: list[str] = []
    lines.append(
        f"**{top.symbol}** in `{top.file_path}` (lines {top.start_line}-{top.end_line}) "
        f"is the strongest match for \"{query}\" with a hybrid score of {round(top.score * 100)}%."
    )
    if len(results) > 1:
        secondary = ", ".join(f"`{r.symbol}` ({r.file_path})" for r in results[1:4])
        lines.append(f"Related matches: {secondary}.")

    relations: list[str] = []
    if top.callers:
        relations.append(f"called by {', '.join(f'`{c}`' for c in top.callers)}")
    if top.callees:
        relations.append(f"calls {', '.join(f'`{c}`' for c in top.callees)}")
    if relations:
        lines.append(f"It is {' and '.join(relations)}.")

    expanded = [symbol for symbol in context_symbols if symbol not in {r.symbol for r in results}]
    if expanded:
        lines.append(f"Graph expansion added {len(expanded)} additional symbol(s): {', '.join(f'`{s}`' for s in expanded[:8])}.")

    lines.append(
        f"\nContext assembled from {len(results)} AST chunk(s) via {vector_backend} vector search "
        f"and the {graph_backend} symbol graph."
    )
    return "\n\n".join(lines)


def llm_answer(query: str, context: str, settings) -> str | None:
    """Call an OpenAI-compatible chat endpoint; returns None on any failure."""
    if not settings.llm_api_key:
        return None
    try:
        import httpx

        response = httpx.Client(timeout=settings.llm_timeout_seconds).post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={
                "model": settings.llm_model,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Question: {query}\n\nCode context:\n{context}"},
                ],
            },
        )
        response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"]).strip()
    except Exception:  # noqa: BLE001 - the extractive answer is the safety net
        return None


def build_citations(results: list[SearchResult]) -> list[Citation]:
    """Citations for the top results, suitable for UI footnote rendering."""
    return [
        Citation(
            symbol=result.symbol,
            file_path=result.file_path,
            start_line=result.start_line,
            end_line=result.end_line,
            repository_id=result.repository_id,
        )
        for result in results[:5]
    ]


def synthesize_answer(
    query: str,
    results: list[SearchResult],
    context_symbols: list[str],
    settings,
    vector_backend: str,
    graph_backend: str,
) -> tuple[str, list[Citation]]:
    """Produce the final answer string plus citations."""
    context = build_context_blocks(results, context_symbols)
    generated = llm_answer(query, context, settings) if results else None
    answer = generated or extractive_answer(query, results, context_symbols, vector_backend, graph_backend)
    return answer, build_citations(results)
