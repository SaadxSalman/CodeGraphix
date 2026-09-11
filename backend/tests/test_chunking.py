"""Unit tests for AST chunking and relationship extraction."""

from app.chunking import chunk_file, extract_calls, extract_imports, relationships_from_chunks

TS_PAYMENT = b'''import { PrismaClient } from "@prisma/client";
import { StripeGateway } from "./stripe-gateway";

/** Handles payment authorization and capture for checkout sessions. */
export class PaymentService {
  private customers: CustomerRepository;
  private gateway: StripeGateway;

  async charge(userId: string, amount: number) {
    const customer = await this.customers.findByUser(userId);
    return this.gateway.capture(customer.paymentMethod, amount);
  }

  async refund(paymentId: string, amount?: number) {
    return this.gateway.refund(paymentId, amount);
  }
}
'''

PY_MODULE = b'''import os
from typing import Any

class Pipeline(BaseService):
    def run(self, doc):
        return self.parse_document(doc)

    def parse_document(self, doc):
        return doc.splitlines()

def embed_corpus(items):
    return [hash(item) for item in items]
'''


def _chunk(path, source, **kwargs):
    return chunk_file(
        source=source,
        file_path=path,
        tenant_id="acme",
        repository_id="atlas-api",
        commit_sha="abc123",
        max_chars=kwargs.pop("max_chars", 1600),
        **kwargs,
    )


class TestChunkerBehavior:
    def test_ts_class_and_methods_with_qualified_names(self):
        chunks = _chunk("src/payments/service.ts", TS_PAYMENT)
        kinds = {(c.kind, c.symbol) for c in chunks}
        assert ("class", "PaymentService") in kinds
        assert ("method", "PaymentService.charge") in kinds
        assert ("method", "PaymentService.refund") in kinds

    def test_line_ranges_are_monotonic(self):
        chunks = _chunk("src/payments/service.ts", TS_PAYMENT)
        for chunk in chunks:
            assert chunk.start_line <= chunk.end_line
            assert chunk.start_byte < chunk.end_byte

    def test_doc_comment_extracted(self):
        chunks = _chunk("src/payments/service.ts", TS_PAYMENT)
        klass = next(c for c in chunks if c.symbol == "PaymentService")
        assert "payment authorization" in (klass.doc or "").lower()

    def test_calls_and_imports_extracted(self):
        chunks = _chunk("src/payments/service.ts", TS_PAYMENT)
        charge = next(c for c in chunks if c.symbol == "PaymentService.charge")
        assert "customers.findByUser" in charge.calls
        assert "gateway.capture" in charge.calls
        assert "@prisma/client" in charge.imports
        assert "./stripe-gateway" in charge.imports

    def test_python_ast_chunker(self):
        chunks = _chunk("services/pipeline.py", PY_MODULE)
        symbols = {c.symbol for c in chunks}
        assert "Pipeline" in symbols
        assert "Pipeline.run" in symbols
        assert "Pipeline.parse_document" in symbols
        assert "embed_corpus" in symbols

    def test_python_class_bases_map_to_extends(self):
        chunks = _chunk("services/pipeline.py", PY_MODULE)
        klass = next(c for c in chunks if c.symbol == "Pipeline")
        assert klass.extends == ["BaseService"]

    def test_chunk_ids_are_stable(self):
        first = _chunk("src/payments/service.ts", TS_PAYMENT)
        second = _chunk("src/payments/service.ts", TS_PAYMENT)
        assert [c.id for c in first] == [c.id for c in second]

    def test_large_declaration_is_split(self):
        big = b"export function huge() {\n" + b"  const x = 1;\n" * 400 + b"}\n"
        chunks = _chunk("src/huge.ts", big, max_chars=800)
        main = [c for c in chunks if c.symbol == "huge"]
        assert len(main) > 1
        assert all(c.part_count == len(main) for c in main)
        assert all(c.part_index > 0 for c in main)

    def test_unsupported_language_returns_empty(self):
        assert _chunk("README.txt", b"hello world") == []


class TestRelationshipExtraction:
    def test_extract_calls_dedupes_and_skips_keywords(self):
        calls = extract_calls(
            "return await this.service.doThing(1) + await this.service.doThing(2); if (x) { helper(); }",
            "Worker.run",
        )
        assert calls.count("service.doThing") == 1
        assert "helper" in calls
        assert all(call not in calls for call in ("if", "return", "await"))

    def test_self_and_this_prefixes_stripped(self):
        calls = extract_calls("this.gateway.capture()", "PaymentService.charge")
        assert "gateway.capture" in calls

    def test_relationships_from_chunks(self):
        chunks = _chunk("src/payments/service.ts", TS_PAYMENT)
        rels = relationships_from_chunks(chunks)
        call_pairs = {(r.source, r.target) for r in rels if r.relation == "CALLS"}
        assert any(s == "PaymentService.charge" and t == "gateway.capture" for s, t in call_pairs)

    def test_imports_extraction_python_and_ts(self):
        assert "os" in extract_imports("import os\n", "python")
        assert "./local" in extract_imports('import "./local";\n', "typescript")
        assert "requests" in extract_imports("import requests\n", "python")


class TestIdempotency:
    def test_rechunking_same_revision_is_identical(self):
        one = _chunk("a.ts", TS_PAYMENT)
        two = _chunk("a.ts", TS_PAYMENT)
        assert [(c.symbol, c.start_byte, c.end_byte) for c in one] == [
            (c.symbol, c.start_byte, c.end_byte) for c in two
        ]