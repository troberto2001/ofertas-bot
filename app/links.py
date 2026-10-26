"""Resolve links encurtados e converte o produto para o teu link de afiliado.

Regra de ouro: nunca se publica o link de outro afiliado. Se a conversão não
for possível, o bot avisa-te e não publica nada.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MAX_HOPS = 8

_META_REFRESH = re.compile(
    r"""<meta[^>]+http-equiv=["']?refresh["']?[^>]+content=["']?\s*\d+\s*;\s*url=([^"'>\s]+)""",
    re.I,
)
_JS_REDIRECT = [
    re.compile(r"""(?:window\.|document\.|top\.)?location(?:\.href)?\s*=\s*["']([^"']+)["']""", re.I),
    re.compile(r"""location\.(?:replace|assign)\(\s*["']([^"']+)["']""", re.I),
]


@dataclass
class Rule:
    name: str
    host_re: str          # regex aplicada ao host
    id_re: str            # regex aplicada ao caminho; grupo 1 = ID do produto
    template: str | None  # o TEU link, com {id} no lugar do ID do produto


HACOO_ID_RE = r"/(?:detail|p)/(\d+)"          # a Hacoo usa /detail/ID e /p/ID
_OLD_HACOO_ID_RE = r"/detail/(\d+)"           # versão anterior (migra-se sozinha)

DEFAULT_RULES = [
    Rule(
        name="hacoo",
        host_re=r"(^|\.)hacoo\.",
        id_re=HACOO_ID_RE,
        template=None,
    )
]


# ---------- regras (ficheiro rules.json) ----------
def load_rules(path: Path) -> list[Rule]:
    if not path.exists():
        fresh = [replace(r) for r in DEFAULT_RULES]  # cópias: nunca partilhar objetos mutáveis
        save_rules(path, fresh)
        return fresh
    data = json.loads(path.read_text("utf-8"))
    rules = [Rule(**r) for r in data]
    migrated = False
    for r in rules:
        if r.name == "hacoo" and r.id_re == _OLD_HACOO_ID_RE:
            r.id_re = HACOO_ID_RE
            migrated = True
    if migrated:
        save_rules(path, rules)
    return rules


def save_rules(path: Path, rules: list[Rule]) -> None:
    path.write_text(json.dumps([asdict(r) for r in rules], indent=2, ensure_ascii=False), "utf-8")


def match_rule(url: str, rules: list[Rule]) -> Rule | None:
    host = (urlsplit(url).hostname or "").lower()
    for r in rules:
        if re.search(r.host_re, host):
            return r
    return None


def extract_id(rule: Rule, url: str) -> str | None:
    m = re.search(rule.id_re, urlsplit(url).path)
    return m.group(1) if m else None


def canonical_url(rule: Rule, url: str) -> str | None:
    """Link limpo do produto, sem parâmetros de afiliado."""
    pid = extract_id(rule, url)
    if not pid:
        return None
    parts = urlsplit(url)
    path_end = re.search(rule.id_re, parts.path).end()  # type: ignore[union-attr]
    return f"{parts.scheme}://{parts.netloc}{parts.path[:path_end]}"


def build_link(rule: Rule, product_id: str, source_url: str | None = None) -> str | None:
    """O teu link para o produto. Se o link de origem usa /p/ID ou /detail/ID,
    o teu link usa o mesmo tipo de página."""
    if not rule.template:
        return None
    tpl = rule.template
    if source_url:
        m = re.search(r"/(detail|p)/\d+", urlsplit(source_url).path)
        if m:
            tpl = re.sub(r"/(?:detail|p)/\{id\}", "/" + m.group(1) + "/{id}", tpl, count=1)
    return tpl.replace("{id}", product_id)


def template_from_sample(rules: list[Rule], sample: str) -> tuple[Rule, str] | None:
    """A partir de um link teu (gerado na tua conta) cria o modelo com {id}."""
    rule = match_rule(sample, rules)
    if not rule:
        return None
    pid = extract_id(rule, sample)
    if not pid:
        return None
    parts = urlsplit(sample)
    m = re.search(rule.id_re, parts.path)
    start, end = m.span(1)  # type: ignore[union-attr]
    new_path = parts.path[:start] + "{id}" + parts.path[end:]
    template = sample.replace(parts.path, new_path, 1)
    return rule, template


# ---------- resolver redirecionamentos ----------
def _body_redirect(body: str, base: str) -> str | None:
    m = _META_REFRESH.search(body)
    if m:
        return urljoin(base, m.group(1))
    for rx in _JS_REDIRECT:
        m = rx.search(body)
        if m and m.group(1).startswith(("http", "/")):
            return urljoin(base, m.group(1))
    return None


async def resolve(url: str, rules: list[Rule], http: httpx.AsyncClient) -> str:
    """Segue redirecionamentos (HTTP, meta refresh, JS simples) até chegar a uma
    loja conhecida. Pára logo aí, sem abrir a página do produto."""
    current = url
    for _ in range(MAX_HOPS):
        if match_rule(current, rules):
            return current
        resp = await http.get(current, headers={"User-Agent": UA})
        if resp.is_redirect and resp.headers.get("location"):
            current = urljoin(current, resp.headers["location"])
            continue
        nxt = _body_redirect(resp.text[:200_000], current)
        if nxt and nxt != current:
            current = nxt
            continue
        return current
    return current


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15, follow_redirects=False)
