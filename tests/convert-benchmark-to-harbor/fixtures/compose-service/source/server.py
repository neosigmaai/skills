"""In-memory ledger HTTP service used by the task."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ENTRIES = []


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True})
        elif self.path == "/entries":
            self._send(200, ENTRIES)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/entries":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        ENTRIES.append(json.loads(self.rfile.read(length)))
        self._send(201, {"ok": True})


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
