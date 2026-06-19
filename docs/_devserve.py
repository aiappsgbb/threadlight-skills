#!/usr/bin/env python3
"""Local preview server for the docs/ Pages site with caching disabled.

`python -m http.server` sends no cache headers, so Edge/Chromium preview panes
heuristically cache pages and ignore reloads while you iterate. This serves the
same static files but forces `Cache-Control: no-store` on every response, so a
refresh always shows the latest edit.

Usage:  python3 docs/_devserve.py [port]   # default port 8799
"""
import http.server, socketserver, os, sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8799

class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

with Server(("127.0.0.1", PORT), NoCacheHandler) as httpd:
    print(f"serving docs/ with no-store on http://localhost:{PORT}")
    httpd.serve_forever()
