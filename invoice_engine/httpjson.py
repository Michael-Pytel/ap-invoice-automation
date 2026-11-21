"""A very small JSON web server and client on Python's standard library (nothing to install).

Used by the simulated Business Central (bc_sim) and the rules service (invoice_engine.service).
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


Route = tuple[str, str, Callable]   # (method, path pattern with {name} parts, handler(params, query, body))


def serve(routes: list[Route], host: str, port: int, name: str) -> None:
    compiled = [(m, re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", p) + "$"), fn) for m, p, fn in routes]

    class Handler(BaseHTTPRequestHandler):
        def _handle(self, method: str) -> None:
            url = urllib.parse.urlsplit(self.path)
            query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw and "json" in (self.headers.get("Content-Type") or "json") else raw
                for m, pattern, fn in compiled:
                    if m == method and (match := pattern.match(url.path)):
                        result = fn(match.groupdict(), query, body)
                        break
                else:
                    raise HttpError(404, f"No such endpoint: {method} {url.path}")
                status, payload = 200, result
            except HttpError as e:
                status, payload = e.status, {"error": e.message}
            except json.JSONDecodeError as e:
                status, payload = 400, {"error": f"Body is not valid JSON: {e}"}
            except Exception as e:                       # never crash the server; say what went wrong
                status, payload = 500, {"error": f"{type(e).__name__}: {e}"}
            if isinstance(payload, tuple):              # (content, content type): HTML page, CSV export
                content, ctype = payload
                data = content.encode("utf-8") if isinstance(content, str) else content
            elif isinstance(payload, (bytes, str)):
                data, ctype = (payload.encode("utf-8") if isinstance(payload, str) else payload), "text/plain; charset=utf-8"
            else:
                data, ctype = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"), "application/json"
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def log_message(self, fmt, *args):
            print(f"[{name}] {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"{name} listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def call(method: str, url: str, body: dict | None = None, timeout: float = 60) -> dict:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read()).get("error", str(e))
        except Exception:
            message = str(e)
        raise HttpError(e.code, message) from None
