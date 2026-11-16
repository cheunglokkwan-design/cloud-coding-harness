#!/usr/bin/env python3
"""Read-only relay for /api/fleet, for exposing the fleet view to the internet.

The harness API has no authentication and can launch agents, so never expose it directly.
This relay polls the local API and serves only the fleet snapshot, behind a shared token:

  python scripts/fleet_relay.py --api http://localhost:8080 --port 8787 --token "$(openssl rand -hex 16)"
  cloudflared tunnel --url http://localhost:8787     # or ngrok http 8787

Clients call GET /fleet with header `X-Fleet-Token: <token>` (or ?token=...).
Standard library only.
"""

from __future__ import annotations

import argparse
import hmac
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

state = {"body": b'{"runs": []}', "at": 0.0}


def poll(api: str, interval: float) -> None:
    while True:
        try:
            with urllib.request.urlopen(f"{api}/api/fleet", timeout=5) as r:
                state["body"], state["at"] = r.read(), time.time()
        except Exception as e:  # keep serving the last snapshot
            print(f"poll failed: {e}")
        time.sleep(interval)


def make_handler(token: str):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            given = self.headers.get("X-Fleet-Token") or parse_qs(u.query).get("token", [""])[0]
            if u.path != "/fleet":
                self.send_error(404)
                return
            if not hmac.compare_digest(given, token):
                self.send_error(401)
                return
            body = state["body"]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    return H


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://localhost:8080")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--token", required=True)
    p.add_argument("--interval", type=float, default=1.0)
    a = p.parse_args()
    if len(a.token) < 16:
        raise SystemExit("use a token of at least 16 characters")
    threading.Thread(target=poll, args=(a.api.rstrip("/"), a.interval), daemon=True).start()
    print(json.dumps({"serving": f"http://0.0.0.0:{a.port}/fleet"}))
    ThreadingHTTPServer(("0.0.0.0", a.port), make_handler(a.token)).serve_forever()


if __name__ == "__main__":
    main()
