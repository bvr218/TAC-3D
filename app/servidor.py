"""Servidor HTTP local para el visor 3D: /visor -> archivos del visor, /estudios -> biblioteca de estudios."""
import os
import posixpath
import threading
import urllib.parse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import config


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, library=None, **k):
        self.library = library
        super().__init__(*a, directory=config.VISOR, **k)

    def translate_path(self, path):
        path = posixpath.normpath(urllib.parse.unquote(path.split("?", 1)[0].split("#", 1)[0]))
        parts = [p for p in path.split("/") if p and p not in (".", "..")]
        if not parts:
            return config.VISOR
        root = {"visor": config.VISOR, "estudios": self.library()}.get(parts[0])
        if root is None:
            return os.path.join(config.VISOR, "__no__")
        return os.path.join(root, *parts[1:])

    def end_headers(self):
        if self.path.split("?")[0].endswith((".json", ".html")):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *a):
        pass


class Server:
    def __init__(self, cfg):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, library=lambda: cfg.library))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, sid=None):
        return f"http://127.0.0.1:{self.port}/visor/index.html" + (f"?e={urllib.parse.quote(sid)}" if sid else "")

    def stop(self):
        self.httpd.shutdown()
