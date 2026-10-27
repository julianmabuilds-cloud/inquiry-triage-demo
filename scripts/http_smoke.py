"""Start a temporary loopback server, assert real HTTP responses, then shut it down."""

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

root = Path(__file__).resolve().parents[1]
server = subprocess.Popen(
    [
        sys.executable,
        "-m",
        "uvicorn",
        "inquiry_triage.api:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
    ],
    cwd=root,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)
try:
    with httpx.Client(base_url="http://127.0.0.1:8765", trust_env=False, timeout=5) as client:
        for _ in range(50):
            if server.poll() is not None:
                raise RuntimeError("Demo server exited before accepting requests")
            try:
                health = client.get("/health")
                break
            except httpx.ConnectError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Server did not become ready")
        assert health.status_code == 200
        print("GET /health:", health.status_code, health.json())
        response = client.post(
            "/triage", json=json.loads((root / "data/inquiries.json").read_text())
        )
        assert response.status_code == 200
        assert response.json()["results"] == json.loads(
            (root / "data/expected/results.json").read_text()
        )
        print("POST /triage:", response.status_code, response.json()["summary"])
        invalid = client.post("/triage", json={"inquiries": []})
        assert invalid.status_code == 422
        print("POST /triage empty batch:", invalid.status_code)
        print("PASS: real localhost HTTP responses match expected fixture outputs")
finally:
    server.terminate()
    try:
        logs, _ = server.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        logs, _ = server.communicate()
    print(logs)
