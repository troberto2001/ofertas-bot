"""Simula o fluxo 'Forçar 5 últimas' sem Telegram: canal público falso, redirecionamento
onlyaff -> Hacoo e um bot falso que regista o que enviaria."""
import asyncio

import httpx

from app import links
from app.config import Config
from app.main import App

THEIRS = "https://www.hacoo.pl/p/{id}?f=p_aff.o_influxmcn.g_affiliate.m_OUTRO.c_x.t_1.v_1&ets=99"
MINE = "https://www.hacoo.pl/detail/{id}?f=p_aff.o_influxmcn.g_affiliate.m_tiago.c_x.t_1.v_1"


def post_html(n: int, kind: str = "offer") -> str:
    if kind == "offer":
        body = (
            f'<div class="tgme_widget_message_text">Tenis modelo {n}<br/><br/>💰 {n}€<br/><br/>'
            f'📲 <a href="https://c.onlyaff.app/AAA{n}">https://c.onlyaff.app/AAA{n}</a></div>'
        )
        photo = f'<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/img{n}.jpg\')"></a>'
    else:
        body = '<div class="tgme_widget_message_text">bom dia pessoal</div>'
        photo = ""
    return f'<div class="tgme_widget_message" data-post="canal/{n}">{photo}{body}</div>'


def handler(req: httpx.Request) -> httpx.Response:
    h, p = req.url.host, req.url.path
    if h == "t.me" and p == "/s/canal":
        html = "".join(post_html(n) for n in range(201, 208)) + post_html(208, "chat")
        return httpx.Response(200, text=html)
    if h == "c.onlyaff.app":
        n = int(p.replace("/AAA", ""))
        return httpx.Response(302, headers={"location": THEIRS.format(id=1000 + n)})
    if h == "cdn.example":
        return httpx.Response(200, content=b"\xff\xd8fakejpeg")
    raise AssertionError(f"pedido inesperado: {req.url}")


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, to, text, **kw):
        self.sent.append(("msg", text, kw))

    async def send_file(self, to, f, caption=None, **kw):
        self.sent.append(("file", caption, kw))


def make_app(tmp_path, monkeypatch, with_template=True):
    for k, v in dict(API_ID="1", API_HASH="x" * 32, BOT_TOKEN="1:x", OWNER_ID="5",
                     DATA_DIR=str(tmp_path), MODE="web", DEFAULT_CODE="COD14").items():
        monkeypatch.setenv(k, v)
    app = App(Config.load())
    app.bot = FakeBot()
    app.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.store.set("web_sources", ["canal"])
    app.store.set("dest", -100123)
    rules = links.load_rules(app.cfg.rules_path)
    if with_template:
        rules[0].template = MINE
        links.save_rules(app.cfg.rules_path, rules)
    return app


def test_force_shows_5_latest_with_my_link(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    app.store.mark_posted("hacoo:1207", 0, 207, "Tenis modelo 207", "u")  # já publicada -> vem marcada
    asyncio.run(app.force_recent(5))

    kinds = [k for k, _, _ in app.bot.sent]
    assert kinds == ["msg", "file", "file", "file", "file", "file", "msg"]  # intro, 5 ofertas, resumo
    offers = [c for k, c, _ in app.bot.sent if k == "file"]
    # as 5 mais recentes (203..207), das mais antigas para a mais nova
    assert [("modelo %d" % n) in c for n, c in zip(range(203, 208), offers)] == [True] * 5
    for n, cap in zip(range(203, 208), offers):
        assert f"/detail/{1000 + n}?f=" in cap or f"/p/{1000 + n}?f=" in cap
        assert "m_tiago" in cap and "m_OUTRO" not in cap and "COD14" in cap
        assert "/p/%d?" % (1000 + n) in cap  # mantém o tipo de página da oferta (/p/)
    assert "Já publicada" in offers[-1] and "Já publicada" not in offers[0]
    # botões Publicar/Rejeitar em cada oferta; nada foi publicado
    assert all(kw.get("buttons") for k, _, kw in app.bot.sent if k == "file")
    assert app.store.count_pending() == 5
    assert app.store.count_since(10) == 1  # só a que marquei à mão
    summary = app.bot.sent[-1][1]
    assert "5 enviada(s)" in summary and "ignorada" in summary  # a 208 não é oferta


def test_force_without_template_explains(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, with_template=False)
    asyncio.run(app.force_recent(2))
    assert not any(k == "file" for k, _, _ in app.bot.sent)
    assert "linkmodelo" in app.bot.sent[-1][1] and "Não consegui converter" in app.bot.sent[-1][1]
    assert app.store.count_pending() == 0


def test_normal_flow_still_skips_duplicates(tmp_path, monkeypatch):
    from app.main import Job
    from app.parser import parse

    app = make_app(tmp_path, monkeypatch)
    app.store.mark_posted("hacoo:1201", 0, 201, "t", "u")
    offer = parse("Tenis modelo 201\n💰 1€", ["https://c.onlyaff.app/AAA201"])
    job = Job(0, 201, offer, photo_url="https://cdn.example/img201.jpg", web=True, origin="o")
    assert asyncio.run(app.process(job)) is None
    assert app.bot.sent == []  # duplicado: ignorado em silêncio
    asyncio.run(app.process(job, force=True))
    assert len(app.bot.sent) == 1 and "Já publicada" in app.bot.sent[0][1]


def test_nolink_test_mode_posts_without_link_and_leaves_no_trace(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, with_template=False)  # sem /linkmodelo feito
    asyncio.run(app.force_recent(5, nolink=True))

    files = [(c, kw) for k, c, kw in app.bot.sent if k == "file"]
    assert len(files) == 5 and app.store.count_pending() == 5
    for cap, kw in files:
        assert cap.startswith("🧪 TESTE (sem link)")
        assert "hacoo" not in cap and "📲" not in cap and "http" not in cap  # sem link
        assert "COD14" in cap and "💰" in cap  # o resto do formato mantém-se
        assert kw.get("buttons")
    assert "sem link" in app.bot.sent[-1][1]

    # carregar em Publicar: vai para o canal de destino, sem link, sem entrar no histórico
    payload = app.store.pop_pending(1)
    assert payload["test"] is True
    app.bot.sent.clear()
    assert asyncio.run(app.publish(payload)) is True
    (kind, caption, _), = app.bot.sent
    assert kind == "file" and "http" not in caption and "COD14" in caption
    assert app.store.count_since(3600) == 0 and app.store.last_post_ts() is None
    assert app.store.recent(10) == []
