# -*- coding: utf-8 -*-
"""Textes affichés : fiches de métadonnées (HTML) et messages d'erreur."""
from __future__ import annotations

from html import escape

from ..manifest import Manifest, ManifestError, Page, safe_html
from ..net import FetchError
from ..politeness import host_of
from ..sources import SourceError


def _row(label: str, value_html: str) -> str:
    return (f'<tr><th align="left" valign="top" style="padding:3px 10px 3px 0;'
            f'white-space:nowrap">{escape(label)}</th>'
            f'<td valign="top" style="padding:3px 0">{value_html}</td></tr>')


def _link(url: str, text: str | None = None) -> str:
    return f'<a href="{escape(url, quote=True)}">{escape(text or url)}</a>'


def _value(value: str) -> str:
    html = safe_html(value)
    stripped = value.strip()
    if stripped.lower().startswith(("http://", "https://")) and " " not in stripped:
        return _link(stripped)
    return html


def manifest_html(manifest: Manifest, tr) -> str:
    parts = [f"<h3>{escape(manifest.label or tr('meta.untitled'))}</h3>"]
    if manifest.summary:
        parts.append(f"<p>{safe_html(manifest.summary)}</p>")
    rows = []
    for label, value in manifest.metadata:
        rows.append(_row(label, _value(value)))
    if manifest.required_statement:
        label, value = manifest.required_statement
        rows.append(_row(label or tr("meta.attribution"), _value(value)))
    if manifest.rights:
        rows.append(_row(tr("meta.rights"), _value(manifest.rights)))
    if manifest.provider:
        rows.append(_row(tr("meta.provider"), escape(manifest.provider)))
    for text, url in manifest.homepages:
        rows.append(_row(tr("meta.homepage"), _link(url, text)))
    rows.append(_row(tr("meta.images"), escape(tr("meta.images_count", n=len(manifest.pages)))))
    rows.append(_row(tr("meta.iiif_version"), str(manifest.version)))
    if manifest.source:
        if manifest.source.lower().startswith(("http://", "https://")):
            rows.append(_row(tr("meta.source"), _link(manifest.source)))
        else:
            rows.append(_row(tr("meta.source"), escape(manifest.source)))
    parts.append("<table>" + "".join(rows) + "</table>")
    return "".join(parts)


def page_html(page: Page, manifest: Manifest | None, tr) -> str:
    total = manifest.canvas_count if manifest else page.index
    title = page.label or tr("meta.untitled")
    parts = [f"<h3>{escape(title)}</h3>"]
    if page.summary:
        parts.append(f"<p>{safe_html(page.summary)}</p>")
    rows = [_row(tr("meta.position"), escape(tr("meta.position_value", index=page.index, total=total)))]
    if page.width and page.height:
        rows.append(_row(tr("meta.dimensions"), escape(tr("meta.dimensions_value", width=page.width, height=page.height))))
    for label, value in page.metadata:
        rows.append(_row(label, _value(value)))
    if page.canvas_id:
        rows.append(_row(tr("meta.canvas"), _value(page.canvas_id)))
    if page.service:
        rows.append(_row(tr("meta.image_service"), _link(page.service.url + "/info.json", page.service.url)))
    elif page.image_url:
        rows.append(_row(tr("meta.image"), _link(page.image_url)))
    parts.append("<table>" + "".join(rows) + "</table>")
    return "".join(parts)


def error_text(exc, tr) -> str:
    """Message traduit pour une erreur de lecture ou de téléchargement."""
    if isinstance(exc, FetchError):
        if exc.kind == "blocked":
            return tr("error.blocked", host=host_of(exc.url) or exc.url, code=exc.status)
        if exc.kind == "http":
            if exc.status in (401, 403):
                return tr("error.forbidden", code=exc.status)
            if exc.status in (404, 410):
                return tr("error.not_found_web", code=exc.status)
            if exc.status == 429:
                return tr("error.too_many_requests")
            return tr("error.http", code=exc.status)
        key = {"timeout": "error.timeout", "too_large": "error.too_large",
               "cancelled": "error.cancelled", "not_image": "error.not_image",
               "not_web": "error.not_web"}.get(exc.kind)
        if key:
            return tr(key)
        return tr("error.network", detail=exc.detail or "")
    if isinstance(exc, SourceError):
        if exc.kind == "empty":
            return tr("error.empty_source")
        if exc.kind == "not_found":
            return tr("error.file_not_found", path=exc.detail)
        return tr("error.not_json")
    if isinstance(exc, ManifestError):
        return tr({"no_images": "error.no_images"}.get(exc.kind, "error.not_manifest"))
    if isinstance(exc, OSError):
        return tr("error.disk", detail=exc.strerror or str(exc))
    return tr("error.unexpected", detail=str(exc))
