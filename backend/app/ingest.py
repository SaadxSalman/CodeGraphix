"""Ingestion pipeline: repository → chunks → vector store + graph.

Source modes:

- ``sample``: index the built-in demo corpus for a tenant (zero setup).
- ``local``: walk a filesystem path with language-aware filtering.
- ``git``: shallow-clone a URL with the ``git`` binary, then walk the clone.

Ingestion is idempotent per ``(tenant, repository)``: the previous revision is
removed before the new one is written, so re-running an ingest never
duplicates vectors, chunks, or graph edges. Progress is tracked through
engine jobs so ``wait=false`` requests can be polled.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .chunking import Chunk, chunk_file, detect_language, is_binary, relationships_from_chunks
from .demo_corpus import corpus_commit_sha, corpus_for
from .stores import EdgeRecord, NodeRecord, VectorRecord


@dataclass
class IngestPlan:
    """Resolved source for one ingestion run."""

    tenant_id: str
    repository_id: str
    repository_name: str
    mode: str
    commit_sha: str
    files: dict[str, bytes] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    files_skipped: int = 0


@dataclass
class IngestOutcome:
    """Result of a completed ingestion run."""

    status: str
    commit_sha: str
    files_scanned: int
    files_indexed: int
    files_skipped: int
    symbols_indexed: int
    chunks_indexed: int
    edges_indexed: int
    warnings: list[str]
    duration_ms: float

def resolve_sample(engine, request) -> IngestPlan:
    tenant = request.tenant_id
    files = corpus_for(tenant)
    plan = IngestPlan(
        tenant_id=tenant,
        repository_id=request.repository_id,
        repository_name=request.repository_name or request.repository_id,
        mode="sample",
        commit_sha=request.commit_sha or corpus_commit_sha(tenant),
    )
    if not files:
        plan.warnings.append(f"No built-in corpus exists for tenant '{tenant}'.")
    for name, content in files.items():
        plan.files[name] = content.encode("utf-8")
    return plan


def resolve_local(engine, request) -> IngestPlan:
    raw_path = request.path
    if not raw_path:
        raise ValueError("mode=local requires `path`.")
    root = Path(raw_path).resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Path does not exist or is not a directory: {root}")
    plan = IngestPlan(
        tenant_id=request.tenant_id,
        repository_id=request.repository_id,
        repository_name=request.repository_name or request.repository_id,
        mode="local",
        commit_sha=request.commit_sha or _content_sha(root),
    )
    _walk_directory(root, engine.settings, plan)
    return plan


def resolve_git(engine, request) -> IngestPlan:
    if not request.git_url:
        raise ValueError("mode=git requires `git_url`.")
    if shutil.which("git") is None:
        raise RuntimeError("The git binary is not available in this environment.")
    clone_dir = Path(tempfile.mkdtemp(prefix="codegraphix-"))
    try:
        command = ["git", "clone", "--depth", "1"]
        if request.branch:
            command += ["--branch", request.branch]
        command += [request.git_url, str(clone_dir / "repo")]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if completed.returncode != 0:
            raise RuntimeError(f"git clone failed: {completed.stderr.strip()[:400]}")
        plan = IngestPlan(
            tenant_id=request.tenant_id,
            repository_id=request.repository_id,
            repository_name=request.repository_name or request.repository_id,
            mode="git",
            commit_sha=request.commit_sha or _git_head_sha(clone_dir / "repo"),
        )
        _walk_directory(clone_dir / "repo", engine.settings, plan)
        return plan
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)


def _content_sha(root: Path) -> str:
    """Stable content hash over the first bytes of each file."""
    from .config import get_settings

    max_bytes = get_settings().ingest_max_file_bytes
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.stat().st_size < max_bytes:
            try:
                digest.update(str(path.relative_to(root)).encode())
                digest.update(path.read_bytes()[:65536])
            except OSError:
                continue
    return digest.hexdigest()[:12]


def _git_head_sha(repo_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if completed.returncode == 0:
            return completed.stdout.strip()[:12]
    except Exception:  # noqa: BLE001
        pass
    return hashlib.sha256(str(repo_root).encode()).hexdigest()[:12]


def _walk_directory(root: Path, settings, plan: IngestPlan) -> None:
    """Collect supported source files under ``root`` into the plan."""
    ignored = set(settings.ignore_dir_list)
    max_files = settings.ingest_max_files
    max_bytes = settings.ingest_max_file_bytes
    scanned = 0
    for path in sorted(root.rglob("*")):
        if scanned >= max_files:
            plan.warnings.append(f"File limit ({max_files}) reached; remaining files skipped.")
            break
        if not path.is_file():
            continue
        if any(part in ignored for part in path.relative_to(root).parts[:-1]):
            continue
        if detect_language(path.name) is None:
            continue
        scanned += 1
        try:
            data = path.read_bytes()
        except OSError as error:
            plan.warnings.append(f"Could not read {path.name}: {error}")
            continue
        if len(data) > max_bytes:
            plan.files_skipped += 1
            plan.warnings.append(f"{path.name} exceeds {max_bytes} bytes; skipped.")
            continue
        if is_binary(data):
            continue
        relative = str(path.relative_to(root)).replace("\\", "/")
        plan.files[relative] = data

def run_ingest(engine, request, job_id: str | None = None) -> dict:
    """Execute the full ingestion pipeline for one request.

    Returns a dict matching ``IngestResponse`` so it can be returned directly
    by the API or stored as a job result.
    """
    started = time.perf_counter()
    try:
        if request.mode == "sample":
            plan = resolve_sample(engine, request)
        elif request.mode == "local":
            plan = resolve_local(engine, request)
        elif request.mode == "git":
            plan = resolve_git(engine, request)
        else:
            raise ValueError(f"Unknown ingest mode: {request.mode}")
    except Exception as error:  # noqa: BLE001 - surfaced as a failed response/job
        duration = round((time.perf_counter() - started) * 1000, 2)
        if job_id:
            engine.finish_job(job_id, status="failed", error=str(error))
        return {
            "status": "failed",
            "tenant_id": request.tenant_id,
            "repository_id": request.repository_id,
            "commit_sha": None,
            "mode": request.mode,
            "job_id": job_id,
            "message": str(error),
            "stats": None,
            "duration_ms": duration,
        }

    all_chunks: list[Chunk] = []
    warnings = list(plan.warnings)
    for file_path, data in plan.files.items():
        try:
            all_chunks.extend(
                chunk_file(
                    source=data,
                    file_path=file_path,
                    tenant_id=plan.tenant_id,
                    repository_id=plan.repository_id,
                    commit_sha=plan.commit_sha,
                    max_chars=engine.settings.chunk_max_chars,
                )
            )
        except Exception as error:  # noqa: BLE001 - one bad file never kills an ingest
            warnings.append(f"{file_path}: parse error ({error.__class__.__name__})")

    # Idempotency: replace the previous revision entirely.
    engine.vector.delete_repository(plan.tenant_id, plan.repository_id)
    engine.graph.delete_repository(plan.tenant_id, plan.repository_id)
    engine.docs.delete_repository(plan.tenant_id, plan.repository_id)

    records: list[VectorRecord] = []
    nodes: list[NodeRecord] = []
    for chunk in all_chunks:
        embedding = engine.embedder.embed(f"{chunk.symbol} {chunk.kind} {chunk.content}")
        payload = {
            "tenant_id": chunk.tenant_id,
            "repository_id": chunk.repository_id,
            "commit_sha": chunk.commit_sha,
            "symbol": chunk.symbol,
            "kind": chunk.kind,
            "language": chunk.language,
            "file_path": chunk.file_path,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "doc": chunk.doc,
            "tokens": embedding.tokens[:400],
        }
        records.append(
            VectorRecord(
                id=chunk.id,
                tenant_id=chunk.tenant_id,
                repository_id=chunk.repository_id,
                commit_sha=chunk.commit_sha,
                dense=embedding.dense,
                sparse_indices=embedding.sparse_indices,
                sparse_values=embedding.sparse_values,
                payload=payload,
            )
        )
        nodes.append(
            NodeRecord(
                name=chunk.symbol,
                kind=chunk.kind,
                file_path=chunk.file_path,
                repository_id=chunk.repository_id,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
            )
        )

    symbols = {chunk.symbol for chunk in all_chunks}
    edge_records: list[EdgeRecord] = []
    for chunk in all_chunks:
        if chunk.symbol == "<module>":
            continue
        for callee in chunk.calls:
            edge_records.append(EdgeRecord(chunk.symbol, "CALLS", callee))
        for base in chunk.extends:
            edge_records.append(EdgeRecord(chunk.symbol, "EXTENDS", base))
        for iface in chunk.implements:
            edge_records.append(EdgeRecord(chunk.symbol, "IMPLEMENTS", iface))
    edge_records = [edge for edge in edge_records if edge.target != edge.source]

    engine.docs.put(all_chunks)
    engine.vector.upsert(records)
    graph_nodes = [node for node in nodes if node.name != "<module>"]
    engine.graph.upsert_symbols(plan.tenant_id, plan.repository_id, graph_nodes)
    edges_added = engine.graph.upsert_edges(plan.tenant_id, plan.repository_id, edge_records)

    engine.register_repository(
        tenant_id=plan.tenant_id,
        repository_id=plan.repository_id,
        name=plan.repository_name,
        commit_sha=plan.commit_sha,
        files=len(plan.files),
        chunks=len(all_chunks),
        symbols=len(symbols),
        edges=edges_added,
        languages=sorted({chunk.language for chunk in all_chunks}),
        mode=plan.mode,
    )

    duration = round((time.perf_counter() - started) * 1000, 2)
    stats = {
        "files_scanned": len(plan.files) + plan.files_skipped,
        "files_indexed": len(plan.files),
        "files_skipped": plan.files_skipped,
        "symbols_indexed": len(symbols),
        "chunks_indexed": len(all_chunks),
        "edges_indexed": edges_added,
        "warnings": warnings[:20],
    }
    result = {
        "status": "indexed",
        "tenant_id": plan.tenant_id,
        "repository_id": plan.repository_id,
        "commit_sha": plan.commit_sha,
        "mode": plan.mode,
        "job_id": job_id,
        "message": f"Indexed {len(plan.files)} files -> {len(symbols)} symbols, {len(all_chunks)} chunks, {edges_added} edges.",
        "stats": stats,
        "duration_ms": duration,
    }
    if job_id:
        engine.finish_job(job_id, status="done", result=result)
    return result


def spawn_ingest(engine, request, job_id: str) -> None:
    """Run an ingestion job on a background thread."""
    import threading

    thread = threading.Thread(target=run_ingest, args=(engine, request, job_id), daemon=True)
    thread.start()



