import sys

sys.path.insert(0, "backend")

from fastapi.testclient import TestClient

from app.main import app

with TestClient(app) as client:
    payload = client.post(
        "/v1/search",
        json={"tenant_id": "acme", "query": "Where is payment authorization handled?", "limit": 8, "expand_graph": True},
    ).json()
    print("MODE:", payload["mode"], "latency:", payload["latency_ms"], "ms")
    print()
    for index, result in enumerate(payload["results"], start=1):
        print(f"{index}: {result['symbol']:38} score={result['score']} dense={result['dense_score']} sparse={result['sparse_score']}")
    print()
    print("ANSWER:")
    print(payload["answer"])
    print()
    print("CITATIONS:", [(c["symbol"], c["file_path"]) for c in payload["citations"]])
    print("CONTEXT SYMBOLS:", len(payload["context_symbols"]), "GRAPH EDGES:", len(payload["graph_edges"]))
    print()
    globex = client.post(
        "/v1/search",
        json={"tenant_id": "globex", "query": "VectorIndexer embed corpus", "limit": 4},
    ).json()
    print("GLOBEX demo query top:", [r["symbol"] for r in globex["results"]])