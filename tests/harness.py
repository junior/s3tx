"""Test support: load s3tx as a module, and a local HTTP server that stands in for the
mediator and for presigned URLs. Nothing here talks to AWS or to the internet."""

import importlib.machinery
import importlib.util
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

REPO = Path(__file__).resolve().parent.parent
S3TX = REPO / "s3tx"
TX = REPO / "tx"

ADMIN_TOKEN = "test-admin-token"
CLIENT_ID = "test-client-secret"  # what a far end sends as X-Client-Id
CLIENT = "laptop"  # the name the mediator knows that far end by

PRIVATE = {
    "BlockPublicAcls": True,
    "IgnorePublicAcls": True,
    "BlockPublicPolicy": True,
    "RestrictPublicBuckets": True,
}


def load_s3tx(name="s3tx_under_test"):
    """Import the s3tx script (no .py, PEP 723 header) as a fresh module. It reads its
    S3TX_* settings at import time, so load it after the environment is in place."""
    loader = importlib.machinery.SourceFileLoader(name, str(S3TX))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def url_lifetime(url):
    """Seconds a presigned URL has left: SigV4 carries X-Amz-Expires, SigV2 an epoch."""
    q = parse_qs(urlsplit(url).query)
    if "X-Amz-Expires" in q:
        return int(q["X-Amz-Expires"][0])
    return int(q["Expires"][0]) - int(time.time())


class FakeServer:
    """A local stand-in for the mediator, plus a tiny file host.

    Mediator side, the protocol s3tx and tx speak:
      POST /api/register  Authorization: Bearer ADMIN_TOKEN, JSON {code, url, client,
                          method, ttl} -> 200 {"ok": true, "ttl": clamped}, 401, or 409
                          when the code is already live
      GET /api/get/CODE   X-Client-Id: CLIENT_ID -> 200 text/plain URL, single use, or a
                          4xx with a short text reason
    File side, standing in for presigned URLs: GET and PUT /files/NAME, GET /manifest.
    """

    def __init__(self):
        self.requests = []  # every request: method, path, headers, body
        self.codes = {}  # code -> {"url", "client", "method", "ttl"}
        self.files = {}  # name -> bytes
        self.manifest = ""
        self.register_replies = []  # queued (status, payload) answers for /api/register
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # keep test output quiet
                pass

            def _reply(self, status, body=b"", ctype="text/plain; charset=utf-8"):
                if isinstance(body, str):
                    body = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status, payload):
                self._reply(status, json.dumps(payload), "application/json")

            def _record(self, read_body=True):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if read_body and n else b""
                server.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": body,
                    }
                )
                return body

            def do_POST(self):
                body = self._record()
                if self.path != "/api/register":
                    return self._reply(404, "no such endpoint")
                if self.headers.get("Authorization") != f"Bearer {ADMIN_TOKEN}":
                    return self._json(401, {"error": "unauthorized"})
                if server.register_replies:
                    return self._json(*server.register_replies.pop(0))
                req = json.loads(body)
                if req["code"] in server.codes:
                    return self._json(409, {"error": "code exists"})
                ttl = min(max(int(req.get("ttl") or 600), 30), 900)
                server.codes[req["code"]] = {
                    "url": req["url"],
                    "client": req["client"],
                    "method": req.get("method", "GET"),
                    "ttl": ttl,
                }
                return self._json(200, {"ok": True, "ttl": ttl})

            def do_GET(self):
                self._record()
                path = urlsplit(self.path).path
                if path.startswith("/api/get/"):
                    if self.headers.get("X-Client-Id") != CLIENT_ID:
                        return self._reply(403, "unknown client")
                    code = path[len("/api/get/") :]
                    entry = server.codes.get(code)
                    if entry is None:
                        return self._reply(404, "not found or expired")
                    if entry["client"] != CLIENT:
                        return self._reply(403, "not your code")
                    del server.codes[code]
                    return self._reply(200, entry["url"])
                if path == "/manifest":
                    return self._reply(200, server.manifest)
                name = path[len("/files/") :] if path.startswith("/files/") else None
                if name in server.files:
                    return self._reply(200, server.files[name], "application/octet-stream")
                return self._reply(404, "NoSuchKey")

            def do_PUT(self):
                if "chunked" in (self.headers.get("Transfer-Encoding") or ""):
                    self._record(read_body=False)
                    self.close_connection = True
                    return self._reply(501, "chunked uploads are not supported")  # like S3
                body = self._record()
                path = urlsplit(self.path).path
                if not path.startswith("/files/"):
                    return self._reply(403, "AccessDenied")
                server.files[path[len("/files/") :]] = body
                return self._reply(200)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()
