import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import httpx

from app import links, parser, render
from app.store import Store

SAMPLE_MSG = """Adidas Spezial - colores especiales

💰 25€

🖤 Código descuento 14% en tu primer pedido: ABC

📲 https://c.onlyaff.app/cW5Uak"""

THEIRS = "https://www.hacoo.pl/detail/39088013?f=p_aff.o_influxmcn.g_affiliate.m_marcgimenezm.c_bt0.t_2026.v_1"
MINE = "https://www.hacoo.pl/detail/111?f=p_aff.o_influxmcn.g_affiliate.m_tiago.c_x.t_2026.v_1"


# ---------- parser ----------
def test_parse_offer():
    o = parser.parse(SAMPLE_MSG)
    assert o.title == "Adidas Spezial - colores especiales"
    assert o.price == "25€"
    assert o.urls == ["https://c.onlyaff.app/cW5Uak"]


def test_parse_hidden_url_and_telegram_filter():
    o = parser.parse("Tenis\n💰 19,99 €", ["https://x.com/p/1", "https://t.me/canal"])
    assert o.urls == ["https://x.com/p/1"]
    assert o.price == "19,99€"


def test_parse_not_an_offer():
    assert parser.parse("bom dia pessoal") is None
    assert parser.parse("https://x.com/a") is None  # sem título


def test_url_trailing_punct():
    assert parser.parse("Tenis\nver https://x.com/a).").urls == ["https://x.com/a"]


# ---------- links ----------
def rules(template=None):
    r = links.Rule("hacoo", r"(^|\.)hacoo\.", links.HACOO_ID_RE, template)
    return [r]


P_FORM = "https://www.hacoo.pl/p/11859290?f=p_aff.o_influxmcn.g_affiliate.m_agimenezmartin.c_aog.t_2026.v_1&ets=1792942152116"


def test_p_form_links_and_path_prefix():
    rs = rules()
    r = links.match_rule(P_FORM, rs)
    assert links.extract_id(r, P_FORM) == "11859290"
    assert links.canonical_url(r, P_FORM) == "https://www.hacoo.pl/p/11859290"
    # modelo guardado a partir de um link /detail/: para uma oferta /p/ usa /p/
    r.template = "https://www.hacoo.pl/detail/{id}?f=MEU"
    assert links.build_link(r, "11859290", P_FORM) == "https://www.hacoo.pl/p/11859290?f=MEU"
    assert links.build_link(r, "39088013", THEIRS) == "https://www.hacoo.pl/detail/39088013?f=MEU"
    assert links.build_link(r, "5") == "https://www.hacoo.pl/detail/5?f=MEU"  # sem origem: igual ao modelo
    # modelo guardado a partir de um link /p/
    rule, tpl = links.template_from_sample(rs, "https://www.hacoo.pl/p/1?f=MEU")
    assert tpl == "https://www.hacoo.pl/p/{id}?f=MEU"


def test_old_rules_file_migrates():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "rules.json"
        old = links.Rule("hacoo", r"(^|\.)hacoo\.", r"/detail/(\d+)", "https://www.hacoo.pl/detail/{id}?f=MEU")
        links.save_rules(p, [old])
        got = links.load_rules(p)[0]
        assert got.id_re == links.HACOO_ID_RE and got.template.endswith("?f=MEU")
        assert links.extract_id(got, P_FORM) == "11859290"
        assert links.load_rules(p)[0].id_re == links.HACOO_ID_RE  # ficheiro foi regravado


def test_match_extract_canonical():
    rs = rules()
    r = links.match_rule(THEIRS, rs)
    assert r and links.extract_id(r, THEIRS) == "39088013"
    assert links.canonical_url(r, THEIRS) == "https://www.hacoo.pl/detail/39088013"
    assert links.match_rule("https://amazon.es/dp/1", rs) is None


def test_template_from_sample_and_build():
    rs = rules()
    rule, tpl = links.template_from_sample(rs, MINE)
    assert tpl == "https://www.hacoo.pl/detail/{id}?f=p_aff.o_influxmcn.g_affiliate.m_tiago.c_x.t_2026.v_1"
    rule.template = tpl
    built = links.build_link(rule, "39088013")
    assert built.startswith("https://www.hacoo.pl/detail/39088013?f=")
    assert "m_tiago" in built and "marcgimenezm" not in built


def test_no_template_means_no_link():
    assert links.build_link(rules()[0], "1") is None


def test_rules_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "rules.json"
        rs = links.load_rules(p)
        assert p.exists() and rs[0].name == "hacoo"
        rs[0].template = "https://h/detail/{id}?a=b"
        links.save_rules(p, rs)
        assert links.load_rules(p)[0].template == "https://h/detail/{id}?a=b"


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_resolve_http_redirect_chain():
    def h(req):
        if req.url.host == "c.onlyaff.app":
            return httpx.Response(302, headers={"location": "https://t.example/go?x=1"})
        if req.url.host == "t.example":
            return httpx.Response(301, headers={"location": THEIRS})
        raise AssertionError("não devia abrir a loja: " + str(req.url))

    out = asyncio.run(links.resolve("https://c.onlyaff.app/cW5Uak", rules(), _client(h)))
    assert out == THEIRS


def test_resolve_js_and_meta_redirect():
    def h(req):
        if req.url.host == "a.example":
            return httpx.Response(200, text=f'<script>window.location.href = "{THEIRS}";</script>')
        if req.url.host == "b.example":
            return httpx.Response(200, text=f'<meta http-equiv="refresh" content="0; url={THEIRS}">')
        raise AssertionError

    assert asyncio.run(links.resolve("https://a.example/x", rules(), _client(h))) == THEIRS
    assert asyncio.run(links.resolve("https://b.example/x", rules(), _client(h))) == THEIRS


def test_resolve_unknown_store_stays():
    def h(req):
        return httpx.Response(200, text="<html>ola</html>")

    out = asyncio.run(links.resolve("https://amazon.es/dp/1", rules(), _client(h)))
    assert links.match_rule(out, rules()) is None


# ---------- render ----------
class MessageEntityCustomEmoji:  # mesmo nome da classe do Telethon
    def __init__(self, offset, length, document_id):
        self.offset, self.length, self.document_id = offset, length, document_id


def ent(offset, length, doc):
    return MessageEntityCustomEmoji(offset, length, doc)


def test_default_render():
    vals = {"titulo": "Adidas", "preco": "25€", "codigo": "ABC", "link": "https://x"}
    text, ents = render.render(render.DEFAULT_TEMPLATE, vals, premium=True)
    assert "💰 25€" in text and text.endswith("📲 https://x") and "ABC" in text
    assert ents == []


def test_missing_price_drops_line():
    vals = {"titulo": "Adidas", "preco": None, "codigo": "ABC", "link": "https://x"}
    text, _ = render.render(render.DEFAULT_TEMPLATE, vals, premium=False)
    assert "💰" not in text
    assert "\n\n\n" not in text


def test_premium_emoji_offsets_utf16():
    # "😀" ocupa 2 unidades UTF-16; o emoji premium "💰" também (2)
    msg = "😀 {titulo}\n💰 {preco}\n{link}"
    # 💰 está na posição UTF-16: len("😀 {titulo}\n") = 2+1+8+1 = 12
    e = ent(12, 2, 5368324170671202286)
    tpl = render.from_telegram(msg, [e])
    assert tpl == "😀 {titulo}\n{ce:5368324170671202286:💰} {preco}\n{link}"
    vals = {"titulo": "A", "preco": "9€", "codigo": "C", "link": "L"}
    text, ents = render.render(tpl, vals, premium=True)
    assert text == "😀 A\n💰 9€\nL"
    # "😀 A\n" = 2+1+1+1 = 5
    assert ents == [(5, 2, 5368324170671202286)]
    text2, ents2 = render.render(tpl, vals, premium=False)
    assert text2 == text and ents2 == []
    assert render.to_plain(tpl) == msg


def test_template_must_have_link():
    assert render.has_link_field("a {link}")
    assert not render.has_link_field("sem campo")


# ---------- store ----------
def test_store_dedup_pending_sources():
    with tempfile.TemporaryDirectory() as d:
        s = Store(Path(d) / "t.db")
        assert not s.was_posted("hacoo:1", 24)
        s.mark_posted("hacoo:1", -100, 5, "t", "u")
        assert s.was_posted("hacoo:1", 24) and not s.was_posted("hacoo:2", 24)
        assert s.count_since(3600) == 1 and s.last_post_ts()

        pid = s.add_pending("hacoo:9", {"a": 1})
        assert s.has_pending_key("hacoo:9") and s.count_pending() == 1
        assert s.pop_pending(pid) == {"a": 1} and s.pop_pending(pid) is None

        s.add_source(-1001, "Grupo")
        assert s.get_source(-1001).active and s.set_topic(-1001, 7)
        assert s.get_source(-1001).topic_id == 7
        s.remove_source(-1001)
        assert s.get_source(-1001) is None

        s.set("mode", "auto")
        assert s.get("mode") == "auto" and s.get("x", 3) == 3
