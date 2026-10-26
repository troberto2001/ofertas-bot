"""Leitura de canais PÚBLICOS do Telegram pela pré-visualização web (t.me/s/<canal>).

Não precisa de conta nem de entrar no canal. Só funciona com canais públicos
que tenham pré-visualização web ativa.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx
from bs4 import BeautifulSoup

from .links import UA
from .parser import Offer, parse

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_RESERVED = {"joinchat", "addlist", "addstickers", "proxy", "socks", "share", "login", "iv"}
_BG_URL = re.compile(r"url\(['\"]?([^'\")]+)['\"]?\)")


class WebFeedError(Exception):
    pass


@dataclass
class WebPost:
    channel: str
    id: int
    text: str
    urls: list[str] = field(default_factory=list)
    photo_url: str | None = None


def normalize_channel(raw: str) -> str | None:
    """'https://t.me/s/nome', 't.me/nome', '@nome' -> 'nome'. Convites privados -> None."""
    s = (raw or "").strip()
    s = re.sub(r"^(?:https?://)?(?:www\.)?t\.me/(?:s/)?", "", s)
    s = s.lstrip("@").split("/")[0].split("?")[0]
    if s.lower() in _RESERVED:
        return None
    return s if NAME_RE.match(s) else None


def parse_html(channel: str, html: str) -> list[WebPost]:
    soup = BeautifulSoup(html, "html.parser")
    posts: list[WebPost] = []
    for w in soup.select("div.tgme_widget_message[data-post]"):
        ref = w.get("data-post", "")
        m = re.match(rf"^{re.escape(channel)}/(\d+)$", ref, re.I)
        if not m:
            continue  # reencaminhadas/outros canais embutidos
        text, urls = "", []
        el = w.select_one(".tgme_widget_message_text")
        if el:
            for br in el.find_all("br"):
                br.replace_with("\n")
            text = el.get_text().strip()
            urls = [a["href"] for a in el.find_all("a", href=True)]
        photo = None
        ph = w.select_one("a.tgme_widget_message_photo_wrap")
        if ph and ph.get("style"):
            mm = _BG_URL.search(ph["style"])
            if mm:
                photo = mm.group(1)
        posts.append(WebPost(channel, int(m.group(1)), text, urls, photo))
    return posts


def recent_offers(
    posts: list[WebPost], n: int, require_photo: bool
) -> tuple[list[tuple[WebPost, Offer]], int]:
    """As n ofertas mais recentes (mais novas primeiro) e quantas mensagens foram ignoradas
    por não serem ofertas (sem link/título) ou por não terem imagem."""
    picked: list[tuple[WebPost, Offer]] = []
    skipped = 0
    for p in sorted(posts, key=lambda x: x.id, reverse=True):
        if len(picked) >= n:
            break
        if require_photo and not p.photo_url:
            skipped += 1
            continue
        offer = parse(p.text, p.urls)
        if not offer:
            skipped += 1
            continue
        picked.append((p, offer))
    return picked, skipped


async def fetch(http: httpx.AsyncClient, channel: str) -> list[WebPost]:
    r = await http.get(f"https://t.me/s/{channel}", headers={"User-Agent": UA})
    if r.status_code != 200:
        raise WebFeedError(
            f"o Telegram respondeu {r.status_code} (canal inexistente, privado ou sem pré-visualização web)"
        )
    posts = parse_html(channel, r.text)
    if not posts:
        raise WebFeedError("não vejo mensagens (canal vazio, privado ou sem pré-visualização web)")
    return posts
