"use client";

import { FormEvent, useState } from "react";
import { Activity, Box, ChevronDown, Code2, Database, GitBranch, LayoutDashboard, Search, Settings, Sparkles, Upload, Users } from "lucide-react";

type Result = { symbol: string; file_path: string; kind: string; score: number; snippet: string; callers: string[]; callees: string[] };

const initialResults: Result[] = [
  { symbol: "PaymentService.charge", file_path: "src/payments/service.ts", kind: "method", score: .97, snippet: "async charge(userId: string, amount: number) {\n  const customer = await this.customers.findByUser(userId);\n  return this.gateway.capture(customer.paymentMethod, amount);\n}", callers: ["CheckoutController.create"], callees: ["CustomerRepository.findByUser", "StripeGateway.capture"] },
  { symbol: "CheckoutController.create", file_path: "src/checkout/controller.ts", kind: "method", score: .89, snippet: "async create(request: CheckoutRequest) {\n  const order = await this.orders.create(request.items);\n  await this.payments.charge(request.userId, order.total);\n  return order;\n}", callers: [], callees: ["OrderRepository.create", "PaymentService.charge"] },
  { symbol: "CustomerRepository.findByUser", file_path: "src/customers/repository.ts", kind: "method", score: .82, snippet: "async findByUser(userId: string): Promise<Customer> {\n  return this.db.customer.findUniqueOrThrow({ where: { userId } });\n}", callers: ["PaymentService.charge"], callees: [] },
  { symbol: "StripeGateway.capture", file_path: "src/payments/stripe-gateway.ts", kind: "method", score: .77, snippet: "capture(paymentMethod: string, amount: number) {\n  return this.stripe.paymentIntents.create({ amount, payment_method: paymentMethod });\n}", callers: ["PaymentService.charge"], callees: [] },
];

export default function Home() {
  const [query, setQuery] = useState("Where is payment authorization handled?");
  const [results, setResults] = useState(initialResults);
  const [searched, setSearched] = useState(false);

  async function runSearch(event?: FormEvent) {
    event?.preventDefault();
    setSearched(true);
    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/v1/search`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tenant_id: "acme", query, limit: 8, expand_graph: true }) });
      if (response.ok) { const data = await response.json(); setResults(data.results); }
    } catch { /* Demo mode keeps the workspace useful without the API. */ }
  }

  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">C</span> CodeGraphix</div>
      <div><div className="eyebrow">Workspace</div><nav className="nav">
        <button className="active"><LayoutDashboard size={15} /> Overview</button><button><Search size={15} /> Ask codebase</button><button><GitBranch size={15} /> Repositories</button><button><Database size={15} /> Knowledge graph</button>
      </nav><div className="eyebrow" style={{ marginTop: 32 }}>Manage</div><nav className="nav"><button><Users size={15} /> Members</button><button><Settings size={15} /> Settings</button></nav></div>
      <div className="tenant"><div className="eyebrow">Current tenant</div><div className="tenant-row"><span>Acme Engineering <ChevronDown size={14} /></span><span className="avatar">AE</span></div></div>
    </aside>
    <main className="workspace">
      <header className="topbar"><div className="path"><strong>Acme Engineering</strong> <span>/</span> Code intelligence</div><div className="top-actions"><button className="icon-button" aria-label="Activity"><Activity size={17} /></button><button className="primary"><Upload size={14} style={{ verticalAlign: "-2px", marginRight: 6 }} /> Index repository</button></div></header>
      <section className="intro"><div><h1>Understand the code<br /><em>behind</em> the code.</h1><p className="subtitle">Ask questions across your repositories with AST-aware chunks and the dependency context your vector search usually misses.</p></div><div className="health"><span className="dot" /> All systems operational</div></section>
      <form className="search-box" onSubmit={runSearch}><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} aria-label="Ask your codebase" /><button className="primary" type="submit"><Sparkles size={14} style={{ verticalAlign: "-2px", marginRight: 5 }} /> Ask</button></form>
      <div className="quick-row"><button className="chip" onClick={() => setQuery("Where is payment authorization handled?")}>Payment flow</button><button className="chip" onClick={() => setQuery("What calls the checkout controller?")}>Callers of checkout</button><button className="chip" onClick={() => setQuery("How is a customer loaded?")}>Customer lookup</button></div>
      <div className="metric-grid"><div className="metric"><div className="metric-label">Indexed symbols</div><div className="metric-value">24,891</div><div className="metric-note">Across 6 repositories</div></div><div className="metric"><div className="metric-label">Graph edges</div><div className="metric-value">61,204</div><div className="metric-note">Calls, imports & inheritance</div></div><div className="metric"><div className="metric-label">Last indexed</div><div className="metric-value">4m</div><div className="metric-note">atlas-api · main branch</div></div><div className="metric"><div className="metric-label">Query latency</div><div className="metric-value">184ms</div><div className="metric-note">Hybrid + graph expansion</div></div></div>
      <div className="section-head"><h2>{searched ? "Retrieved context" : "Example retrieval"}</h2><span>{results.length} AST chunks · graph expanded</span></div>
      <div className="results-layout"><div className="result-list">{results.map((result) => <article className="result" key={result.symbol}><div><div className="result-kind">{result.kind}</div><div className="result-symbol">{result.symbol}</div><div className="result-file mono">{result.file_path}</div><p className="result-snippet mono">{result.snippet}</p></div><div className="score">{Math.round(result.score * 100)}%</div></article>)}</div><aside className="graph-panel"><h3>Dependency context</h3><p>Graph expansion adds the definitions surrounding your best vector matches.</p><div className="graph-visual"><div className="graph-line line-one" /><div className="graph-line line-two" /><div className="graph-line line-three" /><div className="graph-node node-a">Customer<br />repo</div><div className="graph-node node-main">Payment<br />service</div><div className="graph-node node-b">Checkout<br />controller</div><div className="graph-node node-c">Stripe<br />gateway</div></div><div className="context"><div className="context-label">Injected into prompt</div><div className="context-list"><span className="context-tag">CustomerRepository</span><span className="context-tag">StripeGateway</span><span className="context-tag">CheckoutController</span></div></div></aside></div>
    </main>
  </div>;
}
