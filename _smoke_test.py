import sys

sys.path.insert(0, "backend")

from fastapi.testclient import TestClient

from app.main import app

with TestClient(app) as client:
    health = client.get("/health").json()
    print("health:", health)

    response = client.post(
        "/v1/search",
        json={"tenant_id": "acme", "query": "Where is payment authorization handled?", "limit": 8},
    ).json()
    print("mode:", response["mode"], "| latency:", response["latency_ms"])
    for result in response["results"][:5]:
        print(
            " -", result["symbol"], "|", result["file_path"],
            f"L{result['start_line']}-{result['end_line']}",
            "| score", result["score"],
            "| callers", result["callers"],
            "| callees", result["callees"],
        )
    print("context:", response["context_symbols"][:8])
    print("edges:", [(e["source"], e["relation"], e["target"]) for e in response["graph_edges"][:6]])
    print("answer:", response["answer"][:400].replace("\n", " | "))

    stats_acme = client.get("/v1/stats", params={"tenant_id": "acme"}).json()
    print("stats acme:", stats_acme)
    stats_globex = client.get("/v1/stats", params={"tenant_id": "globex"}).json()
    print("stats globex:", stats_globex)

    iso = client.post(
        "/v1/search",
        json={"tenant_id": "globex", "query": "payment authorization charge stripe"},
    ).json()
    print("globex searching payment terms -> results:", len(iso["results"]))

    repos = client.get("/v1/repositories", params={"tenant_id": "acme"}).json()
    print("repos:", [(x["repository_id"], x["files"], x["symbols"], x["languages"]) for x in repos])

    ingest = client.post(
        "/v1/ingest", json={"tenant_id": "acme", "repository_id": "atlas-api"}
    ).json()
    print("re-ingest idempotency:", ingest["message"])

    # Async job path
    job = client.post(
        "/v1/ingest",
        json={"tenant_id": "globex", "repository_id": "orbit-core", "wait": False},
    ).json()
    print("queued job:", job["job_id"], job["status"])
    status = client.get(f"/v1/ingest/{job['job_id']}").json()
    print("job status:", status["status"])

    # Graph snapshot
    snapshot = client.get("/v1/graph", params={"tenant_id": "acme"}).json()
    print("graph snapshot nodes:", len(snapshot["nodes"]), "edges:", len(snapshot["edges"]))
