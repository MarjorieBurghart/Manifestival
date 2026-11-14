# -*- coding: utf-8 -*-
"""Gallica (Bibliothèque nationale de France), sans Qt ni réseau.

- :func:`manifest_url` : l'adresse d'une page de Gallica telle qu'on la voit
  dans le navigateur (``https://gallica.bnf.fr/ark:/12148/btv1b…/f3.item``)
  donne celle du manifest IIIF du document.
- :func:`highres_url` : pour une image de Gallica, l'adresse de l'API image
  ``.highres``, que la BnF cite parmi ses services (api.bnf.fr) avec une
  limite de 50 appels par minute, contre 5 pour les images IIIF en pleine
  taille. Manifestival la demande d'abord pour les téléchargements en pleine
  taille, l'image IIIF restant le second recours.
"""
from __future__ import annotations

import re

HOST = "gallica.bnf.fr"
_ARK = r"ark:/12148/([A-Za-z0-9]+)"
# Toute page de Gallica qui désigne un document : …/ark:/12148/<doc>,
# …/iiif/ark:/…, la visionneuse …/view3if/ga/ark:/…, etc.
_PAGE = re.compile(r"^https?://gallica\.bnf\.fr/(?:[\w.-]+/)*" + _ARK + r"(?:[/.?#].*)?$")
_IIIF_IMAGE = re.compile(r"^https?://gallica\.bnf\.fr/iiif/" + _ARK + r"/f(\d+)(?:/.*)?$")


def manifest_url(address: str) -> str | None:
    """Manifest IIIF d'une adresse de Gallica (page du document, page
    d'une vue, image…) ; None si l'adresse n'est pas une page de document
    de Gallica, ou si c'est déjà un manifest."""
    address = (address or "").strip()
    if address.endswith("/manifest.json"):
        return None
    match = _PAGE.match(address)
    if not match:
        return None
    return f"https://{HOST}/iiif/ark:/12148/{match.group(1)}/manifest.json"


def highres_url(service_or_image_url: str) -> str | None:
    """Adresse ``.highres`` de la vue désignée par un service ou une image
    IIIF de Gallica (``…/iiif/ark:/12148/<doc>/f<n>…``) ; None ailleurs."""
    match = _IIIF_IMAGE.match(service_or_image_url or "")
    if not match:
        return None
    return f"https://{HOST}/ark:/12148/{match.group(1)}/f{match.group(2)}.highres"
