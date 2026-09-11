"use client";

import { useEffect, useState } from "react";
import {
  Activity,
  ChevronDown,
  Code2,
  Database,
  GitBranch,
  LayoutDashboard,
  Loader,
  Search,
  Settings,
  Sparkles,
  Upload,
  Users,
} from "lucide-react";

type Result = {
  id: string;
  symbol: string;
  file_path: string;
  language: string;
  kind: string;
  repository_id: string;
  score: number;
  dense_score: number;
  sparse_score: number;
  start_line: number;
  end_line: number;
  snippet: string;
  callers: string[];
  callees: string[];
  imports: string[];
  doc: string | null;
};

type GraphEdge = { source: string; relation: string; target: string; resolved: boolean };
type Citation = { symbol: string; file_path: string; start_line: number; end_line: number; repository_id: string };
type Stats = { tenant_id: string; repositories: number; files: number; symbols: number; chunks: number; edges: number; languages: string[] };
type SearchResponse = {
  query: string;
  tenant_id: string;
  results: Result[];
  context_symbols: string[];
  graph_edges: GraphEdge[];
  answer: string;
  citations: Citation[];
  latency_ms: number;
  mode: string;
};

const TENANTS = [
  { id: "acme", label: "Acme Engineering", code: "AE" },
  { id: "globex", label: "Globex Data", code: "GD" },
];

const QUICK_QUERIES = [
  "Where is payment authorization handled?",
  "What calls the checkout controller?",
  "How is a customer loaded?",
  "Where is the billing worker's reconcile defined?",
];

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function Home() {
  const [tenantId, setTenantId] = useState("acme");
  const [query, setQuery] = useState(QUICK_QUERIES[0]);
  const [results, setResults] = useState<Result[]>([]);
  const [contextSymbols, setContextSymbols] = useState<string[]>([]);
  const [graphEdges, setGraphEdges] = useState<GraphEdge[]>([]);
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [health, setHealth] = useState("");
  const [latency, setLatency] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [toastKind, setToastKind] = useState<"ok" | "error">("ok");

  function apiUrl(path: string) {
    return `${API_URL}${path}`;
  }

  useEffect(() => {
    void refreshStats(tenantId);
    void refreshHealth();
  }, [tenantId]);

  useEffect(() => {
    void runSearch(QUICK_QUERIES[0], false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function runSearch(question?: string, interactive = true) {
    const activeQuery = (question ?? query).trim();
    if (!activeQuery) return;
    if (interactive) setQuery(activeQuery);
    setLoading(true);
    setError("");
    setSearched(true);
    try {
      const response = await fetch(apiUrl("/v1/search"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tenant_id: tenantId, query: activeQuery, limit: 8, expand_graph: true, generate_answer: true }),
      });
      if (response.ok) {
        const data: SearchResponse = await response.json();
        setResults(data.results);
        setContextSymbols(data.context_symbols ?? []);
        setGraphEdges(data.graph_edges ?? []);
        setAnswer(data.answer ?? "");
        setCitations(data.citations ?? []);
        setLatency(data.latency_ms);
        if (data.results.length === 0) setError("No matching code found for this question in this tenant's index.");
      } else {
        setError(`API error ${response.status} - is the backend running at ${API_URL}?`);
      }
    } catch {
      setError(`Could not reach the API at ${API_URL}. Start uvicorn, or rely on the demo copy below.`);
    } finally {
      setLoading(false);
    }
  }
  async function refreshStats(tenant: string) {
    try {
      const response = await fetch(apiUrl(`/v1/stats?tenant_id=${encodeURIComponent(tenant)}`));
      if (response.ok) setStats(await response.json());
    } catch {
      /* demo-first UI */
    }
  }

  async function refreshHealth() {
    try {
      const response = await fetch(apiUrl("/health"));
      if (response.ok) {
        const payload = await response.json();
        const vector = payload.services?.vector ?? "memory";
        const graph = payload.services?.graph ?? "memory";
        setHealth(
          payload.status === "ok"
            ? `All systems operational · ${vector} vectors · ${graph} graph`
            : "Degraded",
        );
      }
    } catch {
      setHealth("Offline demo mode - start the FastAPI service for live retrieval");
    }
  }

  async function indexRepository() {
    setToast("Indexing demo corpus ...");
    setToastKind("ok");
    try {
      const response = await fetch(apiUrl("/v1/ingest"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tenant_id: tenantId,
          repository_id: tenantId === "acme" ? "atlas-api" : "orbit-core",
          mode: "sample",
          wait: true,
        }),
      });
      if (response.ok) {
        const payload = await response.json();
        setToastKind(payload.status === "indexed" ? "ok" : "error");
        setToast(payload.status === "indexed" ? `Indexed: ${payload.message}` : `Index failed: ${payload.message}`);
        void refreshStats(tenantId);
      } else {
        setToastKind("error");
        setToast("Ingestion failed. Is the backend running?");
      }
    } catch {
      setToastKind("error");
      setToast("Ingestion failed. Is the backend running?");
    }
    setTimeout(() => setToast(""), 6000);
  }

  function switchTenant(id: string) {
    setTenantId(id);
    setResults([]);
    setContextSymbols([]);
    setGraphEdges([]);
    setAnswer("");
    setSearched(false);
    setError("");
    void runSearch(QUICK_QUERIES[0], false);
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">C</span> CodeGraphix</div>
        <div>
          <div className="eyebrow">Workspace</div>
          <nav className="nav">
            <button className="active"><LayoutDashboard size={15} /> Overview</button>
            <button><Search size={15} /> Ask codebase</button>
            <button><GitBranch size={15} /> Repositories</button>
            <button><Database size={15} /> Knowledge graph</button>
          </nav>
          <div className="eyebrow" style={{ marginTop: 32 }}>Manage</div>
          <nav className="nav">
            <button><Users size={15} /> Members</button>
            <button><Settings size={15} /> Settings</button>
          </nav>
        </div>
        <div className="tenant">
          <div className="eyebrow">Current tenant</div>
          <div className="tenant-row">
            <div className="tenant-switch">
              <select value={tenantId} onChange={(event) => switchTenant(event.target.value)} aria-label="Switch tenant">
                {TENANTS.map((tenant) => (
                  <option key={tenant.id} value={tenant.id}>{tenant.label}</option>
                ))}
              </select>
              <ChevronDown size={14} />
            </div>
            <span className="avatar">{TENANTS.find((tenant) => tenant.id === tenantId)?.code}</span>
          </div>
        </div>
      </aside>
      <main className="workspace">
        <header className="topbar">
          <div className="path">
            <strong>{TENANTS.find((tenant) => tenant.id === tenantId)?.label}</strong> <span>/</span> Code intelligence
          </div>
          <div className="top-actions">
            <button className="icon-button" aria-label="Activity"><Activity size={17} /></button>
            <button className="primary" onClick={indexRepository}><Upload size={14} style={{ verticalAlign: "-2px", marginRight: 6 }} /> Index repository</button>
          </div>
        </header>

        <section className="intro">
          <div>
            <h1>Understand the code<br /><em>behind</em> the code.</h1>
            <p className="subtitle">Ask questions across your repositories with AST-aware chunks and the dependency context your vector search usually misses.</p>
          </div>
          <div className="health"><span className="dot" /> {health || "Connecting to API ..."}</div>
        </section>

        <form className="search-box" onSubmit={(event) => { event.preventDefault(); void runSearch(); }}>
          <Search size={17} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} aria-label="Ask your codebase" placeholder="Ask your codebase ..." />
          <button className="primary" type="submit">
            {loading ? <Loader size={14} style={{ verticalAlign: "-2px", marginRight: 5 }} /> : <Sparkles size={14} style={{ verticalAlign: "-2px", marginRight: 5 }} />}
            {loading ? "Searching" : "Ask"}
          </button>
        </form>

        <div className="quick-row">
          {QUICK_QUERIES.map((quick) => (
            <button key={quick} className="chip" onClick={() => void runSearch(quick)}>
              {quick.length > 34 ? `${quick.slice(0, 34)} ...` : quick}
            </button>
          ))}
        </div>

        {stats && (
          <div className="metric-grid">
            <div className="metric"><div className="metric-label">Indexed symbols</div><div className="metric-value">{stats.symbols.toLocaleString()}</div><div className="metric-note">Tenant {stats.tenant_id}</div></div>
            <div className="metric"><div className="metric-label">Graph edges</div><div className="metric-value">{stats.edges.toLocaleString()}</div><div className="metric-note">{stats.languages.join(", ") || "no languages"}</div></div>
            <div className="metric"><div className="metric-label">AST chunks</div><div className="metric-value">{stats.chunks.toLocaleString()}</div><div className="metric-note">{stats.files} files · {stats.repositories} repo{stats.repositories === 1 ? "" : "s"}</div></div>
            <div className="metric"><div className="metric-label">Query latency</div><div className="metric-value">{latency === null ? "—" : `${Math.round(latency)}ms`}</div><div className="metric-note">Hybrid + graph expansion</div></div>
          </div>
        )}

        {toast && <div className={`toast ${toastKind === "ok" ? "" : "toast-error"}`}>{toast}</div>}

        {searched && (
          <div className="answer-panel">
            <div className="answer-head">
              <div className="answer-title"><Sparkles size={13} style={{ verticalAlign: "-2px", marginRight: 7 }} /> Answer</div>
              <span className="answer-mode">{loading ? "searching ..." : `${results.length} chunks · graph-expanded`}</span>
            </div>
            {answer && !loading ? (
              <div className="answer-body">
                <p className="answer-text">{answer}</p>
                {citations.length > 0 && (
                  <div className="citations">
                    {citations.map((citation, index) => (
                      <button key={citation.symbol} className="citation" title={`${citation.file_path}:${citation.start_line}-${citation.end_line}`}>
                        [{String(index + 1).padStart(2, "0")}] {citation.symbol}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="answer-empty">{loading ? "Retrieving code context ..." : error || ""}</div>
            )}
          </div>
        )}

        <div className="section-head">
          <h2>{searched ? "Retrieved context" : "Example retrieval"}</h2>
          <span>{results.length} AST chunks · {tenantId}</span>
        </div>
        <div className="results-layout">
          <div className="result-list">
            {results.length === 0 && !loading && (
              <div className="empty-state">
                <Code2 size={26} style={{ color: "var(--muted)" }} />
                <p>No chunks retrieved yet. Ask a question above to search the indexed AST chunks and dependency graph.</p>
              </div>
            )}
            {results.map((result) => (
              <article className="result" key={result.id}>
                <div>
                  <div className="result-head">
                    <div className="result-kind">{result.kind} · {result.language}</div>
                    <div className="result-meta">{result.repository_id} · lines {result.start_line}-{result.end_line}</div>
                  </div>
                  <div className="result-symbol">{result.symbol}</div>
                  {result.doc && <p className="result-doc">{result.doc}</p>}
                  <p className="result-snippet mono">{result.snippet}</p>
                  <div className="result-relations">
                    {result.callers.length > 0 && (
                      <span className="relation-label">callers <span className="relation-tags">{result.callers.slice(0, 4).map((caller) => <span className="relation-tag" key={caller}>{caller}</span>)}</span></span>
                    )}
                    {result.callees.length > 0 && (
                      <span className="relation-label">callees <span className="relation-tags">{result.callees.slice(0, 4).map((callee) => <span className="relation-tag" key={callee}>{callee}</span>)}</span></span>
                    )}
                  </div>
                </div>
                <div className="score">{Math.round(result.score * 100)}%</div>
              </article>
            ))}
          </div>

          <aside className="graph-panel">
            <h3>Dependency context</h3>
            <p>Graph expansion adds the definitions surrounding your best vector matches.</p>
            <div className="graph-visual">
              {(() => {
                const primary = results.length > 0 ? results[0] : null;
                const callers = primary?.callers.slice(0, 3) ?? [];
                const callees = primary?.callees.slice(0, 3) ?? [];
                const mainLabel = primary ? shortName(primary.symbol) : "No result";
                const slots = [
                  ...callers.map((caller, index) => ({ key: `c${index}`, name: shortName(caller), style: { left: 2, top: 18 + index * 36, width: 64 } })),
                  { key: "main", name: mainLabel, style: { left: 120, top: 52, width: 76 } },
                  ...callees.map((callee, index) => ({ key: `k${index}`, name: shortName(callee), style: { left: 232, top: 18 + index * 36, width: 64 } })),
                ];
                const mainSlot = slots.find((slot) => slot.key === "main") ?? slots[0];
                return (
                  <>
                    <span className="graph-node node-main" style={{ left: mainSlot.style.left, top: mainSlot.style.top, width: mainSlot.style.width }}>{mainSlot.name}</span>
                    {slots.filter((slot) => slot.key !== "main").map((slot) => (
                      <span key={slot.key} className={`graph-node ${slot.key.startsWith("c") ? "node-left" : "node-right"}`} style={slot.style} title={slot.name}>{slot.name}</span>
                    ))}
                  </>
                );
              })()}
            </div>
            <div className="context">
              <div className="context-label">Injected into prompt</div>
              <div className="context-list">
                {(contextSymbols.length > 0 ? contextSymbols : results.map((result) => result.symbol)).slice(0, 10).map((symbol) => (
                  <span className="context-tag" key={symbol}>{symbol}</span>
                ))}
              </div>
            </div>
            {graphEdges.length > 0 && (
              <div className="edge-list">
                <div className="context-label">Resolved edges</div>
                <ul>
                  {graphEdges.slice(0, 6).map((edge) => (
                    <li key={`${edge.source}-${edge.relation}-${edge.target}`}>
                      <span className="edge-src">{shortName(edge.source)}</span>
                      <span className="edge-rel">{edge.relation.toLowerCase()}</span>
                      <span className="edge-tgt">{shortName(edge.target)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </aside>
        </div>
      </main>
    </div>
  );
}

function shortName(symbol: string) {
  const parts = symbol.split(".");
  return parts.length > 2 ? parts.slice(-2).join(".") : symbol;
}