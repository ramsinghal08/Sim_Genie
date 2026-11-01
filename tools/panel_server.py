#!/usr/bin/env python3
"""Serve panel/ with caching disabled.

Sends no-store headers, ignores conditional requests (no 304s) and redirects
the page to /index.html?v=<mtime>, so a reload always shows the current file.
"""

import argparse
import errno
import functools
import http.server
import os
import socketserver
import sys


class NoStoreHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
        if self._redirect_to_versioned():
            return
        super().do_GET()

    def _redirect_to_versioned(self):
        """Redirect to /index.html?v=<mtime> so a stale cached copy is bypassed.

        Requests that already carry ?v= fall through, so this cannot loop.
        """
        path, _, query = self.path.partition("?")
        if path not in ("/", "/index.html") or "v=" in query:
            return False
        try:
            mtime = int(os.path.getmtime(
                os.path.join(self.directory, "index.html")))
        except OSError:
            return False
        self.send_response(302)
        self.send_header("Location", f"/index.html?v={mtime}")
        self.end_headers()
        return True

    def send_head(self):
        # Drop conditional headers so the base class never answers 304.
        for header in ("If-Modified-Since", "If-None-Match"):
            while header in self.headers:
                del self.headers[header]
        return super().send_head()

    def log_message(self, fmt, *args):
        print(f"  panel: {fmt % args}", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--directory", default=".")
    p.add_argument("--bind", default="0.0.0.0")
    args = p.parse_args()

    handler = functools.partial(NoStoreHandler, directory=args.directory)
    socketserver.TCPServer.allow_reuse_address = True
    try:
        httpd = socketserver.TCPServer((args.bind, args.port), handler)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        # Usually an older panel server still holding the port.
        sys.exit(
            f"port {args.port} is already in use -- an older panel server is "
            f"still running.\n"
            f"It may be serving an outdated page.\n"
            f"Stop it (Ctrl-C in its terminal, or: pkill -f panel_server.py; "
            f"pkill -f http.server) and run this again,\n"
            f"or pick another port: PANEL_PORT={args.port + 1} ./rig panel")

    with httpd:
        print(f"panel on http://localhost:{args.port}/index.html "
              f"(no-store: every reload re-reads {os.path.basename(args.directory)}/)",
              flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
