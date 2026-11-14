# -*- coding: utf-8 -*-
"""Lecture des manifests IIIF Presentation 2 et 3 (sans Qt, sans réseau).

:func:`parse_manifest` transforme le JSON d'un manifest en :class:`Manifest` :
titre, résumé, métadonnées, mentions de droits, et une :class:`Page` par
canvas qui porte une image. Les textes multilingues (« language maps » de la
version 3, objets ``@value``/``@language`` de la version 2) sont résolus dans
la langue demandée, avec repli sur « none », l'anglais, puis la première
langue trouvée.

:func:`collection_entries` liste les manifests d'une collection IIIF.

Seules les adresses http(s) sont gardées pour les images et vignettes : un
manifest ne doit pas pouvoir désigner un fichier de l'ordinateur.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from html import escape


class ManifestError(ValueError):
    """Le JSON n'est pas un manifest exploitable. ``kind`` précise :
    ``not_json_object``, ``not_manifest``, ``no_images``."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class ImageService:
    """Service d'images IIIF : adresse de base (sans ``/info.json``),
    version de l'API Image (1, 2 ou 3) et limites annoncées."""
    url: str
    version: int = 2
    max_width: int | None = None
    max_height: int | None = None
    max_area: int | None = None


@dataclass(frozen=True)
class Page:
    """Un canvas du manifest qui porte une image. ``index`` commence à 1 et
    suit l'ordre du manifest (canvases sans image compris)."""
    index: int
    label: str
    canvas_id: str = ""
    width: int | None = None
    height: int | None = None
    service: ImageService | None = None
    image_url: str = ""
    image_format: str = ""
    thumbnail_url: str = ""
    thumbnail_service: ImageService | None = None
    summary: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def meta(self, label: str) -> str:
        """Valeur du champ de métadonnées ``label`` (casse ignorée), ou ""."""
        wanted = label.strip().casefold()
        for key, value in self.metadata:
            if key.strip().casefold() == wanted:
                return value
        return ""


@dataclass(frozen=True)
class Manifest:
    source: str
    id: str
    label: str
    version: int
    pages: tuple[Page, ...]
    summary: str = ""
    metadata: tuple[tuple[str, str], ...] = ()
    required_statement: tuple[str, str] | None = None
    rights: str = ""
    provider: str = ""
    homepages: tuple[tuple[str, str], ...] = ()
    canvas_count: int = 0
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    def meta(self, label: str) -> str:
        wanted = label.strip().casefold()
        for key, value in self.metadata:
            if key.strip().casefold() == wanted:
                return value
        return ""

    def page_metadata_labels(self) -> list[str]:
        """Noms des champs de métadonnées des pages, dans l'ordre de première
        apparition, sans doublon (casse ignorée)."""
        seen, out = set(), []
        for page in self.pages:
            for key, _ in page.metadata:
                k = key.strip()
                if k and k.casefold() not in seen:
                    seen.add(k.casefold())
                    out.append(k)
        return out


# ---------------------------------------------------------------- textes

def _is_web(url) -> bool:
    return isinstance(url, str) and url.strip().lower().startswith(("http://", "https://"))


def _web(url) -> str:
    return url.strip() if _is_web(url) else ""


def _ident(obj) -> str:
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return str(obj.get("id") or obj.get("@id") or "")
    return ""


def _pick_language(entries: list[tuple[str, list[str]]], lang: str) -> list[str]:
    """Valeurs de la langue préférée parmi ``(langue, valeurs)``."""
    if not entries:
        return []
    lang = (lang or "").lower()

    def matches(code: str, wanted: str) -> bool:
        code = (code or "").lower()
        return code == wanted or code.split("-")[0] == wanted

    for wanted in (lang, "none", "", "und", "en"):
        found = [v for code, vals in entries if matches(code, wanted) for v in vals]
        if found:
            return found
    return entries[0][1]


def text_of(value, lang: str = "en") -> str:
    """Texte d'une valeur IIIF : chaîne, liste, ``{"@value": …}`` (v2) ou
    language map (v3). Plusieurs valeurs sont jointes par « ; »."""
    return "; ".join(t for t in _texts(value, lang) if t)


def _texts(value, lang) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int, float)):
        return [str(value)]
    if isinstance(value, list):
        # v2 : liste de chaînes et/ou d'objets {"@value", "@language"}
        tagged, plain = [], []
        for item in value:
            if isinstance(item, dict) and "@value" in item:
                tagged.append((str(item.get("@language") or ""), [str(item.get("@value") or "")]))
            else:
                plain.extend(_texts(item, lang))
        if tagged:
            # Valeurs sans langue gardées avec la langue choisie.
            return plain + _pick_language(tagged, lang)
        return plain
    if isinstance(value, dict):
        if "@value" in value:
            return [str(value.get("@value") or "")]
        entries = []
        for code, vals in value.items():
            if isinstance(vals, list):
                entries.append((code, [str(v) for v in vals if isinstance(v, (str, int, float))]))
            elif isinstance(vals, (str, int, float)):
                entries.append((code, [str(vals)]))
        return _pick_language(entries, lang)
    return []


def _metadata(items, lang) -> tuple[tuple[str, str], ...]:
    out = []
    for entry in items if isinstance(items, list) else []:
        if not isinstance(entry, dict):
            continue
        label = plain_text(text_of(entry.get("label"), lang)).strip()
        value = text_of(entry.get("value"), lang).strip()
        if label or value:
            out.append((label, value))
    return tuple(out)


# ------------------------------------------------------- HTML des valeurs

_ALLOWED_TAGS = {"a", "b", "br", "i", "p", "small", "span", "sub", "sup", "em", "strong", "u"}
_VOID = {"br"}


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.text: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
            return
        if tag in ("br", "p") and self.text:
            self.text.append("\n")
        if tag not in _ALLOWED_TAGS:
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if _is_web(href) or href.lower().startswith("mailto:"):
                self.out.append(f'<a href="{escape(href, quote=True)}">')
            else:
                self.out.append("<a>")
        else:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        if tag in _ALLOWED_TAGS and tag not in _VOID:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self.skip:
            return
        self.out.append(escape(data, quote=False))
        self.text.append(data)


def is_html(value: str) -> bool:
    """Une valeur IIIF est du HTML si elle commence par « < » et finit par
    « > » (spécification Presentation 3, § 4.4) ; sinon c'est du texte."""
    value = (value or "").strip()
    return value.startswith("<") and value.endswith(">")


def safe_html(value: str) -> str:
    """HTML d'une valeur IIIF réduit aux balises permises par la
    spécification (liens http(s) et mailto seulement, pas d'images ni de
    scripts). Un texte qui n'est pas du HTML (:func:`is_html`) est échappé ; ses retours à la ligne
    deviennent ``<br>``."""
    value = value or ""
    if not is_html(value):
        return escape(value, quote=False).replace("\n", "<br>")
    parser = _Sanitizer()
    parser.feed(value)
    parser.close()
    return "".join(parser.out)


def plain_text(value: str) -> str:
    """Texte d'une valeur IIIF sans ses balises HTML."""
    value = value or ""
    if not is_html(value):
        return value
    parser = _Sanitizer()
    parser.feed(value)
    parser.close()
    return "".join(parser.text).strip()


# ------------------------------------------------------- services d'images

def _int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def image_service(service) -> ImageService | None:
    """Premier service d'images IIIF d'une ressource (objet, liste ou
    simple adresse), ou None."""
    candidates = service if isinstance(service, list) else [service]
    for svc in candidates:
        if isinstance(svc, str) and _is_web(svc):
            return ImageService(svc.strip().rstrip("/"), 2)
        if not isinstance(svc, dict):
            continue
        url = _web(svc.get("id") or svc.get("@id"))
        if not url:
            continue
        kind = str(svc.get("type") or svc.get("@type") or "")
        profile = svc.get("profile")
        profile = " ".join(str(p) for p in profile if isinstance(p, str)) if isinstance(profile, list) else str(profile or "")
        context = str(svc.get("@context") or "")
        signature = " ".join((kind, profile, context))
        if "ImageService1" in kind or "/image/1/" in signature or "image-api/1." in signature:
            version = 1
        elif "ImageService3" in kind or "/image/3/" in signature or "level0" == profile or profile in ("level1", "level2"):
            version = 3
        elif "ImageService2" in kind or "/image/2/" in signature:
            version = 2
        else:
            # Services d'un autre type (authentification, recherche…).
            if kind and "Image" not in kind:
                continue
            version = 2
        return ImageService(url.rstrip("/"), version, _int(svc.get("maxWidth")),
                            _int(svc.get("maxHeight")), _int(svc.get("maxArea")))
    return None


def _thumbnail(value) -> tuple[str, ImageService | None]:
    items = value if isinstance(value, list) else [value]
    for item in items:
        if isinstance(item, str) and _is_web(item):
            return item.strip(), None
        if isinstance(item, dict):
            url = _web(item.get("id") or item.get("@id"))
            svc = image_service(item.get("service"))
            if url or svc:
                return url, svc
    return "", None


# ------------------------------------------------------------- canvases

def _v3_painting_body(canvas) -> dict | None:
    for page in canvas.get("items") or []:
        if not isinstance(page, dict):
            continue
        for annotation in page.get("items") or []:
            if not isinstance(annotation, dict):
                continue
            if annotation.get("motivation") not in (None, "painting", ["painting"]):
                continue
            body = annotation.get("body")
            queue = list(body) if isinstance(body, list) else [body]
            while queue:
                b = queue.pop(0)
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "Choice":
                    queue[:0] = list(b.get("items") or [])
                    continue
                if b.get("type") in (None, "Image") or image_service(b.get("service")):
                    return b
    return None


def _v2_resource(canvas) -> dict | None:
    for image in canvas.get("images") or []:
        if not isinstance(image, dict):
            continue
        resource = image.get("resource")
        if isinstance(resource, dict):
            if resource.get("@type") == "oa:Choice":
                default = resource.get("default")
                if isinstance(default, dict):
                    return default
                items = resource.get("item") or []
                return items[0] if items and isinstance(items[0], dict) else None
            return resource
    return None


def _page(index, canvas, body, lang) -> Page | None:
    service = image_service(body.get("service"))
    image_url = _web(body.get("id") or body.get("@id"))
    if not (service or image_url):
        return None
    thumb_url, thumb_service = _thumbnail(canvas.get("thumbnail"))
    label = plain_text(text_of(canvas.get("label"), lang)).strip()
    return Page(
        index=index,
        label=label,
        canvas_id=_ident(canvas),
        width=_int(body.get("width")) or _int(canvas.get("width")),
        height=_int(body.get("height")) or _int(canvas.get("height")),
        service=service,
        image_url=image_url,
        image_format=str(body.get("format") or ""),
        thumbnail_url=thumb_url,
        thumbnail_service=thumb_service,
        summary=text_of(canvas.get("summary") or canvas.get("description"), lang),
        metadata=_metadata(canvas.get("metadata"), lang),
    )


def _type(data) -> str:
    kind = data.get("type") or data.get("@type") or ""
    if isinstance(kind, list):
        kind = " ".join(str(k) for k in kind)
    return str(kind)


def parse_manifest(data, source: str = "", lang: str = "en") -> Manifest:
    """Manifest IIIF Presentation 2 ou 3 lu depuis son JSON.
    ``ManifestError`` si ce n'est pas un manifest ou s'il n'a aucune image."""
    if not isinstance(data, dict):
        raise ManifestError("not_json_object")
    pages: list[Page] = []
    if isinstance(data.get("items"), list) and "Collection" not in _type(data):
        version = 3
        canvases = data["items"]
        for i, canvas in enumerate(canvases, 1):
            if not isinstance(canvas, dict) or canvas.get("type") not in (None, "Canvas"):
                continue
            body = _v3_painting_body(canvas)
            page = _page(i, canvas, body, lang) if body else None
            if page:
                pages.append(page)
    elif isinstance(data.get("sequences"), list):
        version = 2
        sequence = next((s for s in data["sequences"] if isinstance(s, dict)), {})
        canvases = sequence.get("canvases") or []
        for i, canvas in enumerate(canvases, 1):
            if not isinstance(canvas, dict):
                continue
            body = _v2_resource(canvas)
            page = _page(i, canvas, body, lang) if body else None
            if page:
                pages.append(page)
    else:
        raise ManifestError("not_manifest")
    if not pages:
        raise ManifestError("no_images")

    required = None
    if data.get("requiredStatement"):
        rs = data["requiredStatement"]
        if isinstance(rs, dict):
            required = (plain_text(text_of(rs.get("label"), lang)), text_of(rs.get("value"), lang))
    elif data.get("attribution"):
        required = ("", text_of(data.get("attribution"), lang))

    rights = data.get("rights") or data.get("license") or ""
    if isinstance(rights, list):
        rights = "; ".join(_ident(r) for r in rights)
    elif isinstance(rights, dict):
        rights = _ident(rights)

    provider = "; ".join(
        plain_text(text_of(p.get("label"), lang)) for p in data.get("provider") or []
        if isinstance(p, dict) and p.get("label"))

    homepages = []
    for key in ("homepage", "related"):
        value = data.get(key)
        for item in value if isinstance(value, list) else [value]:
            url = _web(_ident(item))
            if url:
                text = plain_text(text_of(item.get("label"), lang)) if isinstance(item, dict) else ""
                homepages.append((text or url, url))

    return Manifest(
        source=source,
        id=_ident(data),
        label=plain_text(text_of(data.get("label"), lang)).strip(),
        version=version,
        pages=tuple(pages),
        summary=text_of(data.get("summary") or data.get("description"), lang),
        metadata=_metadata(data.get("metadata"), lang),
        required_statement=required,
        rights=str(rights or ""),
        provider=provider,
        homepages=tuple(homepages),
        canvas_count=len(canvases),
        raw=data,
    )


def is_collection(data) -> bool:
    return isinstance(data, dict) and "Collection" in _type(data)


def collection_entries(data, lang: str = "en") -> list[tuple[str, str, bool]]:
    """``(titre, adresse, est_une_collection)`` des membres d'une collection
    IIIF (v2 : ``manifests``, ``collections``, ``members`` ; v3 : ``items``)."""
    out = []
    members = []
    for key in ("items", "members", "collections", "manifests"):
        value = data.get(key)
        if isinstance(value, list):
            members.extend(value)
    seen = set()
    for item in members:
        if not isinstance(item, dict):
            continue
        url = _web(_ident(item))
        if not url or url in seen:
            continue
        seen.add(url)
        kind = _type(item)
        out.append((plain_text(text_of(item.get("label"), lang)) or url, url, "Collection" in kind))
    return out
