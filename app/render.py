"""Modelo da mensagem, com suporte a emojis premium (custom emoji).

Formato guardado do modelo:
    {titulo} {preco} {codigo} {link}      -> campos
    {ce:ID:😀}                            -> emoji premium com ID e emoji de recurso
"""
from __future__ import annotations

import re
from typing import Any, Iterable

DEFAULT_TEMPLATE = (
    "{titulo}\n\n"
    "💰 {preco}\n\n"
    "🖤 Código descuento 14% en tu primer pedido: {codigo}\n\n"
    "📲 {link}"
)

FIELDS = ("titulo", "preco", "codigo", "link")
TOKEN_RE = re.compile(r"\{(titulo|preco|codigo|link)\}|\{ce:(\d+):([^}]*)\}")


def utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def from_telegram(text: str, entities: Iterable[Any] | None) -> str:
    """Converte uma mensagem do Telegram (com emojis premium) para o modelo guardado."""
    raw = text.encode("utf-16-le")
    custom = sorted(
        (e for e in (entities or []) if type(e).__name__ == "MessageEntityCustomEmoji"),
        key=lambda e: e.offset,
    )
    out: list[str] = []
    pos = 0  # em unidades UTF-16
    for e in custom:
        out.append(raw[2 * pos : 2 * e.offset].decode("utf-16-le"))
        fallback = raw[2 * e.offset : 2 * (e.offset + e.length)].decode("utf-16-le")
        out.append("{ce:%d:%s}" % (e.document_id, fallback.replace("}", "")))
        pos = e.offset + e.length
    out.append(raw[2 * pos :].decode("utf-16-le"))
    return "".join(out)


def to_plain(template: str) -> str:
    """Modelo legível (emojis premium trocados pelo emoji de recurso)."""
    return TOKEN_RE.sub(lambda m: m.group(3) if m.group(2) else m.group(0), template)


def has_link_field(template: str) -> bool:
    return "{link}" in template


def render(
    template: str, values: dict[str, str | None], premium: bool
) -> tuple[str, list[tuple[int, int, int]]]:
    """Devolve (texto, [(offset, length, document_id)]) com offsets em UTF-16.

    Linhas cujo campo está vazio (ex.: sem preço) são removidas.
    """
    lines_out: list[str] = []
    for line in template.split("\n"):
        fields_in_line = [m.group(1) for m in TOKEN_RE.finditer(line) if m.group(1)]
        if any(not values.get(f) for f in fields_in_line):
            continue
        lines_out.append(line)

    # remove linhas em branco repetidas / nas pontas
    cleaned: list[str] = []
    for line in lines_out:
        if not line.strip() and (not cleaned or not cleaned[-1].strip()):
            continue
        cleaned.append(line)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    body = "\n".join(cleaned)

    text_parts: list[str] = []
    entities: list[tuple[int, int, int]] = []
    pos = 0
    last = 0
    for m in TOKEN_RE.finditer(body):
        chunk = body[last : m.start()]
        text_parts.append(chunk)
        pos += utf16_len(chunk)
        if m.group(1):
            val = str(values.get(m.group(1)) or "")
            text_parts.append(val)
            pos += utf16_len(val)
        else:
            fallback = m.group(3) or "⭐"
            text_parts.append(fallback)
            if premium:
                entities.append((pos, utf16_len(fallback), int(m.group(2))))
            pos += utf16_len(fallback)
        last = m.end()
    text_parts.append(body[last:])
    return "".join(text_parts), entities
