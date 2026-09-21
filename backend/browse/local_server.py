"""Local dev HTTP server for Lambda A (Map/Browse).

Wraps the framework-agnostic route() in a plain http.server so the
frontend BFF can hit it at http://localhost:8001 during local e2e. This
is a DEV convenience only — in production the handler runs as a Lambda.

Usage:
    set -a; . backend/.env; set +a
    .venv/bin/python -m backend.browse.local_server   # serves :8001
"""
from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from backend.browse import handler as browse_handler
from backend.shared.db import get_pool

PORT = 8001


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quieter logs
        pass

    def _run(self):
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        async def go():
            pool = await get_pool()
            return await browse_handler.route(
                self.command, parsed.path, query, pool=pool
            )

        try:
            resp = asyncio.run(go())
        except Exception as e:  # surface errors as 500 for local debugging
            resp = {
                "statusCode": 500,
                "headers": {"content-type": "application/json"},
                "body": json.dumps({"error": f"{type(e).__name__}: {e}"}),
            }
        self.send_response(resp["statusCode"])
        for k, v in resp["headers"].items():
            self.send_header(k, v)
        self.send_header("access-control-allow-origin", "*")
        self.end_headers()
        self.wfile.write(resp["body"].encode())

    def do_GET(self):
        self._run()


def main():
    print(f"Browse dev server on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
