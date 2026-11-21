"""/hacoo_teste e botão 'Verificar Hacoo': conversão real, sem publicar nada."""
import asyncio
from unittest.mock import AsyncMock

from app.hacoo import HacooError, LoginRequired
from test_force import make_app

GENERATED = "https://c.onlyaff.app/meu-link-teste"


def texts(app):
    return [m[1] for m in app.bot.sent]


def setup(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)  # canal "canal" falso; 207 é a oferta mais recente
    app.store.set("hacoo_generator", True)
    return app


def check(app, link=""):
    async def go():
        await app.hacoo_check(link)
        await app.http.aclose()
    asyncio.run(go())


def test_ok_uses_latest_offer_and_reports_auto_login(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)

    async def fake_convert(owner, url):
        app.hacoo.last_run = {"relogged": True, "retries": 1}
        return GENERATED

    app.hacoo.convert = AsyncMock(side_effect=fake_convert)
    check(app)
    called_url = app.hacoo.convert.await_args.args[1]
    assert "/p/1207" in called_url  # a oferta mais recente do canal, depois do redirecionamento
    final = texts(app)[-1]
    assert "Hacoo OK" in final and GENERATED in final and "Nada foi publicado" in final
    assert "autenticou-se sozinho" in final and "repetiu 1 vez" in final
    assert "t.me/canal" in final
    # não publica, não cria pendentes, não mexe no histórico
    assert app.store.count_pending() == 0 and app.store.recent(10) == [] and app.store.count_since(3600) == 0
    assert not any(k == "file" for k, _, _ in app.bot.sent)
    app.store.db.close()


def test_failure_shows_phase_session_state_and_publishes_nothing(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)
    app.hacoo.convert = AsyncMock(side_effect=HacooError("Conversão Hacoo falhou ou excedeu o tempo limite (fase: criar o link). Não publiquei."))
    check(app)
    final = texts(app)[-1]
    assert "Hacoo com problema" in final and "fase: criar o link" in final
    assert "Autenticação automática: desligada" in final and "Nada foi publicado" in final
    assert app.store.count_pending() == 0 and app.store.recent(10) == []
    app.store.db.close()


def test_login_required_is_reported(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)
    app.hacoo.convert = AsyncMock(side_effect=LoginRequired())
    check(app)
    assert "Sessão Hacoo ausente ou expirada" in texts(app)[-1]
    app.store.db.close()


def test_unexpected_error_never_leaks_its_message(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)
    app.hacoo.convert = AsyncMock(side_effect=RuntimeError("private-cookie-value"))
    check(app)
    assert "private-cookie" not in " ".join(texts(app)) and "Erro inesperado" in texts(app)[-1]
    app.store.db.close()


def test_explicit_link_is_used_and_generator_off_is_flagged(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)
    app.store.set("hacoo_generator", False)
    app.hacoo.convert = AsyncMock(return_value=GENERATED)
    check(app, "https://www.hacoo.pl/p/555?f=p_aff.m_OUTRO")
    assert "/p/555" in app.hacoo.convert.await_args.args[1]
    final = texts(app)[-1]
    assert "o link que enviaste" in final and "desligado" in final
    app.store.db.close()


def test_non_hacoo_link_and_nothing_to_test(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)
    app.hacoo.convert = AsyncMock(return_value=GENERATED)
    check(app, "https://www.amazon.es/dp/1")
    assert "não leva a um produto da Hacoo" in texts(app)[-1]
    app.hacoo.convert.assert_not_awaited()

    app2 = setup(tmp_path / "b", monkeypatch) if (tmp_path / "b").mkdir() is None else None
    app2.store.set("web_sources", [])  # sem origem e sem nada publicado
    app2.hacoo.convert = AsyncMock(return_value=GENERATED)
    check(app2)
    assert "Não tenho nenhum produto para testar" in texts(app2)[-1]
    app2.hacoo.convert.assert_not_awaited()
    app.store.db.close(); app2.store.db.close()


def test_falls_back_to_last_posted_product(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)
    app.store.set("web_sources", [])
    app.store.mark_posted("hacoo:777", 0, 1, "t", "u")
    app.hacoo.convert = AsyncMock(return_value=GENERATED)
    check(app)
    assert "/p/777" in app.hacoo.convert.await_args.args[1]
    assert "último produto que publiquei" in texts(app)[-1]
    app.store.db.close()


def test_command_and_button_are_wired(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)
    assert b"h:1" in [b.to_dict()["type"]["data"] for row in app.main_buttons() for b in row]
    assert callable(app.cmd_hacoo_teste)
    app.hacoo.convert = AsyncMock(return_value=GENERATED)

    async def go():
        await app.cmd_hacoo_teste(None, "")
        await app.http.aclose()

    asyncio.run(go())
    assert "Hacoo OK" in texts(app)[-1]
    app.store.db.close()
