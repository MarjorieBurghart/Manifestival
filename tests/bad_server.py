# -*- coding: utf-8 -*-
"""Serveur HTTP de test qui se comporte mal, pour éprouver le réseau.

Chemins : /hang (ne répond jamais), /slow (répond après ``slow_delay``
secondes), /trickle (envoie un octet toutes les ``trickle_delay``
secondes), /cut (coupe la connexion au milieu de l'image), /error500
(erreur 500), /ok (image normale). ``closed_url()`` : un port où rien
n'écoute (serveur éteint).
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
import time

BODY = b"\xff\xd8\xff\xe0" + bytes(4000) + b"\xff\xd9"


class BadServer:
    def __init__(self, slow_delay=2.0, trickle_delay=0.5):
        self.slow_delay, self.trickle_delay = slow_delay, trickle_delay
        self.stop = threading.Event()
        self.hits: dict[str, int] = {}
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                path = self.path.split("?")[0]
                server.hits[path] = server.hits.get(path, 0) + 1
                try:
                    if path.startswith("/hang"):
                        server.stop.wait(120)
                        return
                    if path.startswith("/slow"):
                        server.stop.wait(server.slow_delay)
                        return self._send(BODY)
                    if path.startswith("/trickle"):
                        self._head(len(BODY))
                        for byte in BODY:
                            if server.stop.wait(server.trickle_delay):
                                return
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                        return
                    if path.startswith("/cut"):
                        self._head(len(BODY))
                        self.wfile.write(BODY[:1000])
                        self.wfile.flush()
                        self.connection.shutdown(socket.SHUT_RDWR)
                        return
                    if path.startswith("/error500"):
                        self.send_response(500)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self._send(BODY)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

            def _head(self, length):
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(length))
                self.end_headers()

            def _send(self, body):
                self._head(len(body))
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()


def closed_url() -> str:
    """Adresse d'un port local où rien n'écoute."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}/image.jpg"


def timed(fn):
    start = time.monotonic()
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001
        result = exc
    return result, time.monotonic() - start
