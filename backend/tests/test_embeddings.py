"""Unit tests for the deterministic embedding engine."""

from app.embeddings import HashingEmbedder, bm25_score, cosine, tokenize


class TestTokenization:
    def test_camel_case_split(self):
        tokens = tokenize("getUserData")
        assert "getuserdata" in tokens
        assert "user" in tokens
        assert "data" in tokens

    def test_lowercase_and_underscore(self):
        tokens = tokenize("update_by_id")
        assert "update" in tokens
        assert "id" in tokens

    def test_dotted_identifier(self):
        tokens = tokenize("StripeGateway.capture")
        lowered = [t for t in tokens]
        assert "stripegateway" in lowered


class TestHashingEmbedder:
    def test_deterministic(self):
        embedder = HashingEmbedder(128)
        one = embedder.embed("async charge(userId) { return gateway.capture(); }")
        two = embedder.embed("async charge(userId) { return gateway.capture(); }")
        assert one.dense == two.dense
        assert one.sparse_indices == two.sparse_indices
        assert one.sparse_values == two.sparse_values

    def test_similar_text_scores_higher_than_unrelated(self):
        embedder = HashingEmbedder(64)
        query = "payment authorization charge capture"
        similar = embedder.embed("capture payment authorization service charge")
        unrelated = embedder.embed("parse yaml configuration file")
        query_vector = embedder.embed_dense(query)
        assert cosine(similar.dense, query_vector) > cosine(unrelated.dense, query_vector)

    def test_sparse_structure(self):
        embedding = HashingEmbedder(64).embed("charge payment capture")
        assert len(embedding.sparse_indices) == len(embedding.sparse_values)
        assert embedding.sparse_indices == sorted(embedding.sparse_indices)
        assert all(value > 0 for value in embedding.sparse_values)

    def test_dense_is_l2_normalized(self):
        embedding = HashingEmbedder(64).embed("PaymentService.charge capture")
        norm = sum(value * value for value in embedding.dense) ** 0.5
        assert abs(norm - 1.0) < 1e-6


class TestBM25:
    def test_bm25_prefers_token_overlap(self):
        idf = {"charge": 1.5, "capture": 1.4, "user": 1.2}
        docs = [
            {"charge": 1, "capture": 1},
            {"user": 3},
        ]
        lengths = [2, 1]
        query = ["charge", "capture"]
        avg = sum(lengths) / len(lengths)
        hit = bm25_score(query, docs[0], lengths[0], avg, idf)
        miss = bm25_score(query, docs[1], lengths[1], avg, idf)
        assert hit > miss
        assert miss == 0.0