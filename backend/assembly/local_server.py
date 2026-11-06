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

from backend import config
from backend.assembly import handler as assembly_handler
from backend.shared import tenant
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
        # AA-827: the BFF's guest token, passed through like the Lambda adapter does.
        guest_token = self.headers.get(config.GUEST_TOKEN_HEADER)
        # AA-1038 slice 2: the same tenant assertion check as the Lambda adapter (a refusal answers before any SQL).
        picked = assembly_handler.tenant_assertion_answer({k.lower(): v for k, v in self.headers.items()})
        chosen = picked.get("tenant", {})
        tid = chosen.get("tenant_id", config.DEFAULT_TENANT_ID)
        source = chosen.get("tenant_source", tenant.SOURCE_DEFAULT)

        async def go():
            pool = await get_pool()
            # AA-1038: the same tenant context as the Lambda (assembly_handler.serve): bound right after acquire,
            # before route(), unbound in a finally.
            async with pool.acquire() as conn, tenant.bound(conn, tid, source=source):
                return await assembly_handler.route(
                    self.command, parsed.path, body, conn=conn, guest_token=guest_token
                )

        try:
            resp = picked["response"] if "response" in picked else asyncio.run(go())
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
