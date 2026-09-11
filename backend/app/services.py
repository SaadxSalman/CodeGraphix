from dataclasses import dataclass
import hashlib
import math
import re


@dataclass(frozen=True)
class Symbol:
    id: str
    tenant_id: str
    repository_id: str
    symbol: str
    file_path: str
    language: str
    kind: str
    snippet: str
    callers: tuple[str, ...]
    callees: tuple[str, ...]


DEMO_SYMBOLS = [
    Symbol("payment-service", "acme", "atlas-api", "PaymentService.charge", "src/payments/service.ts", "TypeScript", "method", """async charge(userId: string, amount: number) {\n  const customer = await this.customers.findByUser(userId);\n  return this.gateway.capture(customer.paymentMethod, amount);\n}""", ("CheckoutController.create",), ("CustomerRepository.findByUser", "StripeGateway.capture")),
    Symbol("checkout-controller", "acme", "atlas-api", "CheckoutController.create", "src/checkout/controller.ts", "TypeScript", "method", """async create(request: CheckoutRequest) {\n  const order = await this.orders.create(request.items);\n  await this.payments.charge(request.userId, order.total);\n  return order;\n}""", (), ("OrderRepository.create", "PaymentService.charge")),
    Symbol("customer-repository", "acme", "atlas-api", "CustomerRepository.findByUser", "src/customers/repository.ts", "TypeScript", "method", """async findByUser(userId: string): Promise<Customer> {\n  return this.db.customer.findUniqueOrThrow({ where: { userId } });\n}""", ("PaymentService.charge",), (),),
    Symbol("stripe-gateway", "acme", "atlas-api", "StripeGateway.capture", "src/payments/stripe-gateway.ts", "TypeScript", "method", """capture(paymentMethod: string, amount: number) {\n  return this.stripe.paymentIntents.create({ amount, payment_method: paymentMethod });\n}""", ("PaymentService.charge",), (),),
    Symbol("order-repository", "acme", "atlas-api", "OrderRepository.create", "src/orders/repository.ts", "TypeScript", "method", """create(items: LineItem[]) {\n  return this.db.order.create({ data: { items, status: 'pending' } });\n}""", ("CheckoutController.create",), (),),
    Symbol("auth-middleware", "acme", "atlas-api", "requireSession", "src/auth/middleware.ts", "TypeScript", "function", """export function requireSession(request: Request) {\n  const token = request.headers.get('authorization');\n  return sessionStore.verify(token);\n}""", ("CheckoutController.create",), ("SessionStore.verify",)),
    Symbol("billing-worker", "acme", "atlas-api", "BillingWorker.reconcile", "workers/billing.ts", "TypeScript", "method", """async reconcile() {\n  const charges = await this.gateway.listRecent();\n  return this.ledger.reconcile(charges);\n}""", (), ("StripeGateway.listRecent", "Ledger.reconcile")),
    Symbol("config-loader", "acme", "atlas-api", "loadConfig", "src/config.ts", "TypeScript", "function", """export function loadConfig() {\n  return parseEnvironment(process.env);\n}""", ("App.bootstrap",), ("parseEnvironment",)),
]


def deterministic_embedding(text: str, dimensions: int = 32) -> list[float]:
    values = []
    for index in range(dimensions):
        digest = hashlib.sha256(f"{index}:{text}".encode()).digest()
        values.append((int.from_bytes(digest[:4], "big") / 2**32) * 2 - 1)
    norm = math.sqrt(sum(value * value for value in values)) or 1
    return [value / norm for value in values]


def lexical_score(query: str, symbol: Symbol) -> float:
    terms = set(re.findall(r"[a-zA-Z0-9_]+", query.lower()))
    haystack = f"{symbol.symbol} {symbol.file_path} {symbol.snippet}".lower()
    hits = sum(term in haystack for term in terms)
    return min(0.99, 0.35 + hits * 0.13)


def search_demo(tenant_id: str, query: str, limit: int) -> list[tuple[Symbol, float]]:
    scoped = [item for item in DEMO_SYMBOLS if item.tenant_id == tenant_id] or DEMO_SYMBOLS
    ranked = sorted(scoped, key=lambda item: lexical_score(query, item), reverse=True)
    return [(item, lexical_score(query, item)) for item in ranked[:limit]]
