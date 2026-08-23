#!/usr/bin/env python3
"""
panel_server.py -- serve panel/ with caching turned off.

WHY NOT `python -m http.server`
-------------------------------
Because a browser tab left open across an edit is a liar, and it cost us most
of a debugging session.

The panel is one static file. You edit it, or pull a fix, and then look at the
tab that is already open. Nothing forces that tab to re-fetch: the DOM in front
of you is the OLD page, still connected to a live rosbridge, still updating the
director table from real messages. Every symptom you are trying to fix is still
on screen, and the obvious conclusion -- "the fix did not work" -- is wrong.

`http.server` makes this worse by answering a conditional request with 304 Not
Modified, so even a soft reload can keep serving what the browser already has.

`no-store` means the browser is not allowed to keep a copy at all: every load,
soft reload included, fetches the file from disk. On a single-file page served
over localhost that costs nothing measurable.

The page also stamps its own Last-Modified into the header, so "am I looking at
the current file?" is answerable at a glance rather than by inference.
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
        """Send the page to a URL that carries the file's mtime.

        Headers can only influence the NEXT fetch; they cannot cause one. A tab
        opened on a cached copy never asks, so it never learns about no-store
        -- and the cache is keyed by URL, so http://0.0.0.0:8000 and
        http://localhost:8000 are two separate stale copies of the same file.

        A redirect breaks that at the only point the browser must obey: the
        navigation itself. /index.html becomes /index.html?v=<mtime>, a URL it
        has never seen when the file has changed, so there is nothing to serve
        from cache. Requests that already carry ?v= fall through, so this
        cannot loop.
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
        # SimpleHTTPRequestHandler answers If-Modified-Since with a 304, which
        # is exactly the behaviour no-store exists to prevent. Dropping the
        # conditional headers before it reads them makes every request
        # unconditional, so the response always carries the current file.
        for header in ("If-Modified-Since", "If-None-Match"):
            while header in self.headers:
                del self.headers[header]
        return super().send_head()

    def log_message(self, fmt, *args):
        # One line per request, without the date noise -- this runs in a
        # terminal someone is watching during a demo.
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
        # The old server is almost always still running from the previous
        # attempt -- and it is serving the OLD file, which is the exact
        # confusion this program exists to end. A traceback here reads as "the
        # new server is broken"; it is not, it is the old one still holding the
        # port and still answering the browser.
        sys.exit(
            f"port {args.port} is already in use -- an older panel server is "
            f"still running.\n"
            f"It is serving the page from BEFORE your last pull, which is why "
            f"a reload changes nothing.\n"
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
