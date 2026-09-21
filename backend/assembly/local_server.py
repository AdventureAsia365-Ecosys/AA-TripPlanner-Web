"""Local dev HTTP server for Lambda B (Trip Assembly).

Wraps the framework-agnostic route() in a plain http.server so the
frontend BFF can hit it at http://localhost:8002 during local e2e. DEV
convenience only — production runs as a Lambda.

Usage:
    set -a; . backend/.env; set +a
    .venv/bin/python -m backend.assembly.local_server   # serves :8002
"""
from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from backend.assembly import handler as assembly_handler
from backend.shared.db import get_pool

PORT = 8002


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _read_body(self) -> dict:
        length = int(self.headers.get("content-length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _run(self):
        parsed = urlparse(self.path)
        body = self._read_body()

        async def go():
            pool = await get_pool()
            async with pool.acquire() as conn:
                return await assembly_handler.route(
                    self.command, parsed.path, body, conn=conn
                )

        try:
            resp = asyncio.run(go())
        except Exception as e:
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

    def do_POST(self):
        self._run()

    def do_DELETE(self):
        self._run()

    def do_PATCH(self):
        self._run()


def main():
    print(f"Assembly dev server on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
