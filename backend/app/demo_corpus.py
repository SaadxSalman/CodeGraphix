"""Built-in demo corpus for instant, deterministic demos.

Each tenant gets a small realistic repository: ``acme/atlas-api`` models an
e-commerce backend in TypeScript, ``globex/orbit-core`` a data platform in
Python and Go. Files are plain strings so the real chunker, embedder, and
stores run over them exactly like user repositories — nothing in the demo
path is faked.
"""

from __future__ import annotations

import hashlib

# --- acme / atlas-api (TypeScript) ---------------------------------------------------

ACME_FILES: dict[str, str] = {}

ACME_FILES["src/payments/service.ts"] = '''import { PrismaClient } from "@prisma/client";
import { StripeGateway } from "./stripe-gateway";
import { CustomerRepository } from "../customers/repository";

/** Handles payment authorization and capture for checkout sessions. */
export class PaymentService {
  private customers: CustomerRepository;
  private gateway: StripeGateway;

  constructor(customers: CustomerRepository, gateway: StripeGateway) {
    this.customers = customers;
    this.gateway = gateway;
  }

  async charge(userId: string, amount: number) {
    const customer = await this.customers.findByUser(userId);
    return this.gateway.capture(customer.paymentMethod, amount);
  }

  async refund(paymentId: string, amount?: number) {
    return this.gateway.refund(paymentId, amount);
  }
}
'''

ACME_FILES["src/checkout/controller.ts"] = '''import { OrderRepository } from "../orders/repository";
import { PaymentService } from "../payments/service";
import { requireSession } from "../auth/middleware";

/** HTTP entry point for the checkout flow. */
export class CheckoutController {
  constructor(private orders: OrderRepository, private payments: PaymentService) {}

  async create(request: CheckoutRequest) {
    requireSession(request.meta);
    const order = await this.orders.create(request.items);
    await this.payments.charge(request.userId, order.total);
    return order;
  }
}
'''

ACME_FILES["src/customers/repository.ts"] = '''import { PrismaClient } from "@prisma/client";

/** Reads customer records, including their default payment method. */
export class CustomerRepository {
  constructor(private db: PrismaClient) {}

  async findByUser(userId: string): Promise<Customer> {
    return this.db.customer.findUniqueOrThrow({ where: { userId } });
  }
}
'''

ACME_FILES["src/payments/stripe-gateway.ts"] = '''import Stripe from "stripe";

/** Thin adapter around the Stripe PaymentIntents API. */
export class StripeGateway {
  constructor(private stripe: Stripe) {}

  capture(paymentMethod: string, amount: number) {
    return this.stripe.paymentIntents.create({ amount, payment_method: paymentMethod, confirm: true });
  }

  refund(paymentId: string, amount?: number) {
    return this.stripe.refunds.create({ payment_intent: paymentId, amount });
  }

  listRecent(customerId: string) {
    return this.stripe.paymentIntents.list({ customer: customerId, limit: 25 });
  }
}
'''

ACME_FILES["src/orders/repository.ts"] = '''import { PrismaClient } from "@prisma/client";

/** Persists orders; orders are created pending until payment succeeds. */
export class OrderRepository {
  constructor(private db: PrismaClient) {}

  create(items: LineItem[]) {
    return this.db.order.create({ data: { items, status: "pending" } });
  }

  async markPaid(orderId: string) {
    return this.db.order.update({ where: { id: orderId }, data: { status: "paid" } });
  }
}
'''

ACME_FILES["src/auth/middleware.ts"] = '''import { sessionStore } from "./store";

/** Rejects requests without a valid session token. */
export function requireSession(meta: RequestMeta) {
  const token = meta.headers.authorization;
  if (!token) throw new UnauthorizedError();
  return sessionStore.verify(token);
}
'''

ACME_FILES["workers/billing.ts"] = '''import { StripeGateway } from "../src/payments/stripe-gateway";
import { Ledger } from "./ledger";

/** Nightly job reconciling captured charges with the internal ledger. */
export class BillingWorker {
  constructor(private gateway: StripeGateway, private ledger: Ledger) {}

  async reconcile() {
    const charges = await this.gateway.listRecent(currentCustomer());
    return this.ledger.reconcile(charges);
  }
}
'''

ACME_FILES["src/config.ts"] = '''/** Loads typed configuration from the process environment. */
export function loadConfig() {
  return parseEnvironment(process.env);
}

function parseEnvironment(env: NodeJS.ProcessEnv) {
  if (!env.DATABASE_URL) throw new Error("DATABASE_URL is required");
  return { databaseUrl: env.DATABASE_URL, stripeKey: env.STRIPE_KEY ?? "" };
}
'''

# --- globex / orbit-core (Python + Go) ------------------------------------------------

GLOBEX_FILES: dict[str, str] = {}

GLOBEX_FILES["services/ingest/pipeline.py"] = '''"""Document ingestion pipeline for the Orbit data platform."""

from services.search.embedder import VectorIndexer


class IngestPipeline:
    def __init__(self, indexer: VectorIndexer):
        self.indexer = indexer

    def run(self, document):
        sections = self.parse_document(document)
        return self.indexer.upsert(document.id, sections)

    def parse_document(self, document):
        return [section for section in document.split("\\n\\n") if section.strip()]
'''

GLOBEX_FILES["services/search/embedder.py"] = '''"""Embedding store for Orbit search."""

from services.search import hashing


class VectorIndexer:
    def __init__(self, dimensions: int = 256):
        self.dimensions = dimensions

    def embed_corpus(self, items):
        return [hashing.embed(item) for item in items]

    def upsert(self, doc_id, sections):
        vectors = self.embed_corpus(sections)
        return {"id": doc_id, "count": len(vectors)}
'''

GLOBEX_FILES["pkg/graph/writer.go"] = '''package graph

import "context"

// GraphWriter persists symbol edges to the dependency store.
type GraphWriter struct {
	dsn string
}

func NewGraphWriter(dsn string) *GraphWriter {
	return &GraphWriter{dsn: dsn}
}

func (w *GraphWriter) WriteEdges(ctx context.Context, edges []Edge) (int, error) {
	return len(edges), nil
}
'''

GLOBEX_FILES["pkg/api/server.go"] = '''package api

import "net/http"

// Server exposes the Orbit platform HTTP API.
type Server struct {
	mux *http.ServeMux
}

func NewServer() *Server {
	return &Server{mux: http.NewServeMux()}
}

func (s *Server) Routes() http.Handler {
	s.mux.HandleFunc("/health", handleHealth)
	return s.mux
}
'''


def corpus_for(tenant_id: str) -> dict[str, str]:
    """Return the built-in demo file map for a tenant."""
    if tenant_id == "acme":
        return ACME_FILES
    if tenant_id == "globex":
        return GLOBEX_FILES
    return {}


def corpus_commit_sha(tenant_id: str) -> str:
    """Deterministic pseudo commit SHA for the demo corpus."""
    joined = "".join(f"{name}:{content}" for name, content in sorted(corpus_for(tenant_id).items()))
    return hashlib.sha256(joined.encode()).hexdigest()[:12]

