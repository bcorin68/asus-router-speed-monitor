#!/usr/bin/env python3
"""A read-only LAN dashboard; never serves private state or SSH keys."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def handler_for(public_root, application_root):
    routes = {
        "/": (application_root / "index.html", "text/html; charset=utf-8"),
        "/index.html": (application_root / "index.html", "text/html; charset=utf-8"),
        "/history.json": (public_root / "history.json", "application/json"),
        "/results.csv": (public_root / "results.csv", "text/csv; charset=utf-8"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/healthz":
                payload, content_type = b"router-speed-monitor\n", "text/plain"
            elif path in routes:
                source, content_type = routes[path]
                try:
                    payload = source.read_bytes()
                except FileNotFoundError:
                    if path == "/history.json":
                        payload = b'{"schema":1,"tests":[]}'
                    elif path == "/results.csv":
                        payload = b"timestamp,status,download_gbps,upload_gbps,latency_ms\n"
                    else:
                        self.send_error(404)
                        return
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'unsafe-inline'; "
                             "style-src 'unsafe-inline'; connect-src 'self'; "
                             "img-src 'self'; frame-ancestors 'self'")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            if args and str(args[1] if len(args) > 1 else "") not in ("200", "304"):
                super().log_message(format, *args)

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/router-speed-monitor.json")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    server = ThreadingHTTPServer(
        (config["listen_address"], config["port"]),
        handler_for(Path(config["state_dir"]) / "www", Path(__file__).resolve().parent))
    server.serve_forever()


if __name__ == "__main__":
    main()
