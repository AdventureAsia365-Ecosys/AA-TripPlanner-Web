"""AA-812 — LOCAL DEV ONLY: the browse Lambda's route() over the catalog fixtures, no database.

Lets the frontend render /, /plan, /tours/[slug], /destinations/[country] for screenshots and UI
work without Dev RDS access. Lives under backend/tests/ so it is never packaged into a Lambda
(package_lambdas.sh drops tests/). Photo paths are served as generated SVG placeholders, so set
CIS_PUBLIC_API_BASE to this server.

Usage (from the repo root):
    CIS_PUBLIC_API_BASE=http://localhost:8011 python3 -m backend.tests.dev_browse_server
    # frontend/.env.local: BROWSE_API_URL=http://localhost:8011
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from backend.browse import handler as browse_handler
from backend.tests.catalog_fixture import FakeCatalogPool

PORT = int(os.environ.get("DEV_BROWSE_PORT", "8011"))  # override when 8011 is taken
_PHOTO_RE = re.compile(r"^/content/photos/([0-9a-f-]+)$")


def _placeholder_svg(seed: str) -> bytes:
    h = hashlib.md5(seed.encode()).hexdigest()
    hue = int(h[:2], 16) * 360 // 255
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1067" viewBox="0 0 1600 1067">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="hsl({hue},45%,62%)"/><stop offset="1" stop-color="hsl({(hue + 40) % 360},40%,32%)"/>'
        '</linearGradient></defs><rect width="1600" height="1067" fill="url(#g)"/>'
        '<path d="M0 800 L400 520 L640 700 L980 420 L1600 860 L1600 1067 L0 1067Z" fill="rgba(0,0,0,.18)"/>'
        '<text x="50%" y="52%" font-family="sans-serif" font-size="44" fill="rgba(255,255,255,.75)" '
        'text-anchor="middle">fixture photo</text></svg>'
    )
    return svg.encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        m = _PHOTO_RE.match(parsed.path)
        if m:
            body = _placeholder_svg(m.group(1))
            self.send_response(200)
            self.send_header("content-type", "image/svg+xml")
            self.send_header("cache-control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
            return
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            resp = asyncio.run(browse_handler.route("GET", parsed.path, query, pool=FakeCatalogPool()))
        except Exception as e:  # surface errors for local debugging
            resp = {"statusCode": 500, "headers": {"content-type": "application/json"},
                    "body": json.dumps({"error": f"{type(e).__name__}: {e}"})}
        self.send_response(resp["statusCode"])
        for k, v in resp["headers"].items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(resp["body"].encode())


def main():
    print(f"Browse FIXTURE dev server on http://localhost:{PORT} (no database)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
