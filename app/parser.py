"""Extrai título, preço e links de uma mensagem de oferta."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

URL_RE = re.compile(r"https?://[^\s<>\"']+")
PRICE_RE = re.compile(r"(\d{1,5}(?:[.,]\d{1,2})?)\s*€|€\s*(\d{1,5}(?:[.,]\d{1,2})?)")
_TRAILING = ".,;:!?)]}»"


@dataclass
class Offer:
    title: str
    price: str | None
    urls: list[str] = field(default_factory=list)


def clean_url(url: str) -> str:
    return url.rstrip(_TRAILING)


def _is_telegram(url: str) -> bool:
    return bool(re.match(r"https?://(www\.)?(t\.me|telegram\.me)/", url, re.I))


def find_urls(text: str, extra: list[str] | None = None) -> list[str]:
    """URLs do texto + as que vêm escondidas em entidades/botões, sem duplicados."""
    found: list[str] = []
    for u in list(extra or []) + URL_RE.findall(text or ""):
        u = clean_url(u)
        if u and not _is_telegram(u) and u not in found:
            found.append(u)
    return found


def _price(text: str) -> str | None:
    lines = text.splitlines()
    # a linha com 💰 tem prioridade
    ordered = [l for l in lines if "💰" in l] + [l for l in lines if "💰" not in l]
    for line in ordered:
        m = PRICE_RE.search(line)
        if m:
            return (m.group(0)).replace(" ", "")
    return None


def _title(text: str) -> str | None:
    for line in text.splitlines():
        s = line.strip()
        if not s or URL_RE.search(s) or "€" in s:
            continue
        return s
    return None


def parse(text: str, extra_urls: list[str] | None = None) -> Offer | None:
    """Devolve a oferta, ou None se a mensagem não for uma oferta (sem link/título)."""
    text = text or ""
    urls = find_urls(text, extra_urls)
    if not urls:
        return None
    title = _title(text)
    if not title:
        return None
    return Offer(title=title, price=_price(text), urls=urls)
