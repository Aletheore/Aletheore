"""Local review only: serves the real Hallbook ranking so the /hallbook page's search box is live.

    PYTHONPATH=../src python3 scripts/hallbook-dev-api.py            # listens on 127.0.0.1:8780
    HALLBOOK_API=http://127.0.0.1:8780 npm run dev                    # the page now calls it through /api/hallbook/*

Without it the page falls back to its recorded examples. Never deployed: the site has no Python runtime.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(__file__))
import hallbook_catalog as hc  # noqa: E402
from aletheore import skill_hub as sh  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query).get("q", [""])[0]
        if url.path == "/api/find":
            body = hc.find(q)
        elif url.path == "/api/search":
            body = [hc.card(m.skill_id, s) for m, s in sh.search_skills_scored(q, limit=8) if m.skill_id in hc.CAT]
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


port = int(sys.argv[1]) if len(sys.argv) > 1 else 8780
print(f"{len(hc.CAT)} skills loaded; http://127.0.0.1:{port}", flush=True)
ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
