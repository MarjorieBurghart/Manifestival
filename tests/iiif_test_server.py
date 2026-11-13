# -*- coding: utf-8 -*-
"""Serveur IIIF de test (127.0.0.1, port libre) et manifests d'exemple."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Signature JPEG seulement : suffit hors de l'interface.
FAKE_JPEG = b"\xff\xd8\xff\xe0" + bytes(32) + b"\xff\xd9"


def real_jpeg(width: int = 60, height: int = 90, color: int = 0x8A6D3B) -> bytes:
    """Vraie image JPEG (Qt), pour les vignettes de l'interface."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImage
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(color)
    array = QByteArray()
    buffer = QBuffer(array)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "JPG", 80)
    buffer.close()
    return bytes(array.data())


def manifest_v3(base: str, count: int = 5) -> dict:
    labels = ["f. 1r", "f. 1v", "f. 2r", "f. 2v", "Reliure / dos"]
    items = []
    for i in range(1, count + 1):
        label = labels[i - 1] if i <= len(labels) else f"f. {i}"
        items.append({
            "id": f"{base}/canvas/{i}", "type": "Canvas",
            "label": {"fr": [label], "en": [label.replace("Reliure / dos", "Binding / spine")]},
            "width": 2000, "height": 3000,
            "metadata": [{"label": {"en": ["Folio"]}, "value": {"none": [f"{i}"]}}],
            "items": [{"id": f"{base}/page/{i}", "type": "AnnotationPage", "items": [{
                "id": f"{base}/anno/{i}", "type": "Annotation", "motivation": "painting",
                "target": f"{base}/canvas/{i}",
                "body": {"id": f"{base}/iiif/img{i}/full/max/0/default.jpg", "type": "Image",
                         "format": "image/jpeg", "width": 2000, "height": 3000,
                         "service": [{"id": f"{base}/iiif/img{i}", "type": "ImageService3",
                                      "profile": "level1"}]},
            }]}],
        })
    return {
        "@context": "http://iiif.io/api/presentation/3/context.json",
        "id": f"{base}/manifest3.json", "type": "Manifest",
        "label": {"fr": ["Psautier de test"], "en": ["Test psalter"]},
        "summary": {"en": ["<p>A <b>test</b> manuscript.<script>alert(1)</script></p>"]},
        "metadata": [
            {"label": {"en": ["Shelfmark"], "fr": ["Cote"]}, "value": {"none": ["Ms. lat. 1234"]}},
            {"label": {"en": ["Date"]}, "value": {"en": ["13th century"], "fr": ["XIIIe siècle"]}},
            {"label": {"en": ["Description"]},
             "value": {"en": ['<span>See <a href="https://example.org/x">the catalogue</a>.</span>']}},
        ],
        "requiredStatement": {"label": {"en": ["Attribution"]}, "value": {"en": ["Test Library"]}},
        "rights": "http://creativecommons.org/publicdomain/mark/1.0/",
        "items": items,
    }


def manifest_v2(base: str) -> dict:
    canvases = []
    for i in range(1, 4):
        canvases.append({
            "@id": f"{base}/v2/canvas/{i}", "@type": "sc:Canvas", "label": f"p. {i}",
            "width": 1000, "height": 1500,
            "images": [{"@type": "oa:Annotation", "motivation": "sc:painting", "resource": {
                "@id": f"{base}/iiif/v2img{i}/full/full/0/default.jpg", "@type": "dctypes:Image",
                "format": "image/jpeg", "width": 1000, "height": 1500,
                "service": {"@context": "http://iiif.io/api/image/2/context.json",
                            "@id": f"{base}/iiif/v2img{i}",
                            "profile": "http://iiif.io/api/image/2/level2.json"}},
                "on": f"{base}/v2/canvas/{i}"}],
        })
    return {
        "@context": "http://iiif.io/api/presentation/2/context.json",
        "@id": f"{base}/manifest2.json", "@type": "sc:Manifest",
        "label": [{"@value": "Livre d'heures", "@language": "fr"},
                  {"@value": "Book of hours", "@language": "en"}],
        "description": "Un manuscrit de test.",
        "attribution": "Bibliothèque de test",
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "metadata": [{"label": "Cote", "value": "Ms 42"},
                     {"label": "Langue", "value": [{"@value": "latin", "@language": "fr"},
                                                   {"@value": "Latin", "@language": "en"}]}],
        "sequences": [{"@type": "sc:Sequence", "canvases": canvases}],
    }


def collection(base: str) -> dict:
    return {
        "@context": "http://iiif.io/api/presentation/3/context.json",
        "id": f"{base}/collection.json", "type": "Collection", "label": {"en": ["Coll"]},
        "items": [{"id": f"{base}/manifest3.json", "type": "Manifest", "label": {"en": ["Psalter"]}},
                  {"id": f"{base}/manifest2.json", "type": "Manifest", "label": {"en": ["Hours"]}}],
    }


class IIIFServer:
    """``image_bytes`` : contenu servi pour toute image ; ``failures`` :
    ``{identifiant: [codes HTTP à renvoyer d'abord]}``."""

    def __init__(self, image_bytes: bytes = FAKE_JPEG):
        self.image_bytes = image_bytes
        self.failures: dict[str, list[int]] = {}
        self.requests: list[str] = []
        self.lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                with server.lock:
                    server.requests.append(self.path)
                base = server.base
                routes = {"/manifest3.json": lambda: manifest_v3(base),
                          "/manifest2.json": lambda: manifest_v2(base),
                          "/collection.json": lambda: collection(base)}
                if self.path in routes:
                    return self._send(200, json.dumps(routes[self.path]()).encode(), "application/json")
                if self.path == "/page.html":
                    return self._send(200, b"<html>not json</html>", "text/html")
                match = re.match(r"^/iiif/([^/]+)/", self.path)
                if match:
                    ident = match.group(1)
                    with server.lock:
                        queue = server.failures.get(ident) or []
                        code = queue.pop(0) if queue else None
                    if code:
                        return self._send(code, b"error", "text/plain", {"Retry-After": "0"})
                    return self._send(200, server.image_bytes, "image/jpeg")
                self._send(404, b"not found", "text/plain")

            def _send(self, code, body, content_type, headers=None):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
