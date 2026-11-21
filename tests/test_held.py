"""Falha do Hacoo: a oferta fica em espera e é publicada sozinha no próximo post."""
import asyncio
import time
from unittest.mock import AsyncMock

from app.hacoo import HacooError, LoginRequired
from app.main import Job
from app.parser import parse
from test_force import make_app

GENERATED = "https://c.onlyaff.app/meu-link"
SRC = "https://www.hacoo.pl/p/1201"


def setup(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    app.store.set("hacoo_generator", True)
    app.store.set("mode", "auto")
    return app


def job(n):
    return Job(0, n, parse(f"Tenis {n}\n25€", [f"https://www.hacoo.pl/p/{1000 + n}"]), web=True,
               origin=f"https://t.me/canal/{n}")


def go(coro):
    return asyncio.run(coro)


def test_failure_holds_offer_and_notifies_once(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)
    app.hacoo.convert = AsyncMock(side_effect=LoginRequired())

    async def run():
        await app.process(job(201))
        await app.process(job(202))  # dentro da janela: não volta a tentar o login
        await app.http.aclose()

    go(run())
    assert app.hacoo.convert.await_count == 1
    assert app.store.held_count() == 2 and app.store.recent(10) == []
    assert sum("fica em espera" in m[1] for m in app.bot.sent) == 1
    app.store.db.close()


def test_next_post_recovers_and_publishes_held_in_order(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)
    app.hacoo.convert = AsyncMock(side_effect=HacooError("Login Hacoo não concluído."))

    async def run():
        await app.process(job(201))
        app.hacoo.convert = AsyncMock(return_value=GENERATED)
        app.hacoo_down_until = 0.0  # passou a janela
        await app.queue.put(job(202))
        w = asyncio.create_task(app.worker())
        await app.queue.join()
        w.cancel()
        await app.http.aclose()

    go(run())
    titles = [r["title"] for r in reversed(app.store.recent(10))]
    assert titles == ["Tenis 201", "Tenis 202"]  # a em espera primeiro, depois a nova
    assert app.store.held_count() == 0
    assert any("recuperou" in m[1] for m in app.bot.sent)
    app.store.db.close()


def test_still_failing_keeps_held_without_loop(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)
    app.hacoo.convert = AsyncMock(side_effect=LoginRequired())

    async def run():
        await app.process(job(201))
        app.hacoo_down_until = 0.0
        n = await app.drain_held()
        await app.http.aclose()
        return n

    assert go(run()) == 0
    assert app.store.held_count() == 1 and app.hacoo.convert.await_count == 2
    app.store.db.close()


def test_held_expire_and_dedup_and_commands(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)
    app.hacoo.convert = AsyncMock(side_effect=LoginRequired())

    class Ev:
        def __init__(self): self.out = []
        async def respond(self, text, **kw): self.out.append(text)

    async def run():
        await app.process(job(201))
        app.hacoo_down_until = 0.0
        app.hacoo.convert.side_effect = LoginRequired()
        await app.process(job(201))  # mesma mensagem: não duplica
        assert app.store.held_count() == 1
        ev = Ev()
        await app.cmd_espera(ev, "")
        assert "1 em espera" in ev.out[-1]
        app.hacoo.convert = AsyncMock(return_value=GENERATED)
        await app.cmd_espera(ev, "agora")
        assert "Retomadas: 1" in ev.out[-1] and app.store.held_count() == 0
        app.hacoo.convert = AsyncMock(side_effect=LoginRequired())
        app.hacoo_down_until = 0.0
        await app.process(job(203))
        app.store.db.execute("UPDATE held SET ts=?", (int(time.time()) - 7 * 3600,))
        await app.cmd_espera(ev, "")
        assert ev.out[-1] == "Nada em espera."
        await app.process(job(204))
        await app.cmd_espera(ev, "limpar")
        assert app.store.held_count() == 0
        await app.http.aclose()

    go(run())
    app.store.db.close()


def test_force_mode_never_holds(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)
    app.hacoo.convert = AsyncMock(side_effect=LoginRequired())
    go(app.process(job(201), force=True))
    assert app.store.held_count() == 0 and app.hacoo_down_until == 0.0
    go(app.http.aclose())
    app.store.db.close()
