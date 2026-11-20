import asyncio
import os
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from app.hacoo import HacooError, HacooGenerator, LoginRequired, SessionVault, product_url, result_url
from app.main import Job
from app.parser import parse
from test_force import make_app

SOURCE = "https://www.hacoo.pl/p/1201"
GENERATED = "https://c.onlyaff.app/my-test-link"


def vault(tmp_path):
    return SessionVault(tmp_path / "session.enc", Fernet.generate_key().decode())


def test_session_encrypted_bound_to_owner_and_key(tmp_path):
    v = vault(tmp_path)
    state = {"cookies": [{"name": "secret", "value": "private-test-cookie"}], "origins": []}
    v.save(5, state)
    assert b"private-test-cookie" not in v.path.read_bytes()
    assert v.load(5)["state"] == state
    with pytest.raises(LoginRequired):
        v.load(6)
    other = SessionVault(v.path, Fernet.generate_key().decode())
    with pytest.raises(LoginRequired):
        other.load(5)
    v.path.write_bytes(v.path.read_bytes()[:-10] + b"tampered")
    with pytest.raises(LoginRequired):
        v.load(5)


def test_expiration_not_extended_by_refresh_and_logout(tmp_path):
    v = vault(tmp_path)
    v.save(5, {}, time.time() - 8 * 86400)
    with pytest.raises(LoginRequired):
        v.load(5)
    asyncio.run(HacooGenerator(v).logout())
    assert not v.path.exists()
    with pytest.raises(HacooError):
        SessionVault(v.path, "").save(5, {})


@pytest.mark.parametrize("url", [
    "http://www.hacoo.pl/p/123", "https://hacoo.pl.evil.test/p/123",
    "https://hacoo.evil.test/p/123", "https://hacoo.pl@evil.test/p/123",
    "https://user@hacoo.pl/p/123", "https://127.0.0.1/p/123",
    "https://hacoo.pl:8443/p/123", "https://hacoo.pl/p/123abc",
])
def test_rejects_untrusted_product_urls(url):
    with pytest.raises(HacooError):
        product_url(url)


def test_strips_previous_affiliate_and_validates_result():
    assert product_url(SOURCE + "?f=someone-else#secret") == SOURCE
    assert result_url(GENERATED, SOURCE) == GENERATED
    assert result_url(SOURCE + "?f=mine", SOURCE) == SOURCE + "?f=mine"
    for value in (SOURCE, "https://evil.test/result", SOURCE.replace("1201", "999") + "?f=mine", GENERATED + "\n" + GENERATED):
        with pytest.raises(HacooError):
            result_url(value, SOURCE)


class Event:
    def __init__(self, text, *, owner=5, private=True, delete_error=False):
        self.raw_text, self.sender_id, self.is_private = text, owner, private
        self.message = SimpleNamespace(fwd_from=None)
        self.respond = AsyncMock()
        self.delete = AsyncMock(side_effect=RuntimeError("private-error") if delete_error else None)


def setup_app(tmp_path, monkeypatch):
    monkeypatch.setenv("HACOO_SESSION_KEY", Fernet.generate_key().decode())
    app = make_app(tmp_path, monkeypatch)
    app.hacoo.login = AsyncMock()
    return app


def test_private_owner_login_deletes_credentials_preserves_password(tmp_path, monkeypatch):
    app = setup_app(tmp_path, monkeypatch)

    async def run():
        for event in (Event("/hacoo_login", owner=99), Event("/hacoo_login", private=False)):
            await app.on_command(event)
            event.respond.assert_not_called()
        await app.on_command(Event("/hacoo_login"))
        assert app.hacoo_step == "email"
        email, password = Event("owner@example.test"), Event(" /secret test ")
        await app.on_command(email)
        await app.on_command(password)
        email.delete.assert_awaited_once()
        password.delete.assert_awaited_once()
        app.hacoo.login.assert_awaited_once_with(5, "owner@example.test", " /secret test ")
        assert app.hacoo_email is None and app.hacoo_step is None
        assert app.s("hacoo_generator") is True
        assert "secret test" not in str(app.store.db.execute("SELECT * FROM settings").fetchall())
        assert "secret test" not in str(password.respond.call_args_list)
        await app.http.aclose()

    asyncio.run(run())
    app.store.db.close()


def test_timeout_cancel_and_delete_failure_never_login(tmp_path, monkeypatch):
    app = setup_app(tmp_path, monkeypatch)

    async def run():
        await app.on_command(Event("/hacoo_login"))
        await app.on_command(Event("owner@example.test"))
        app.hacoo_deadline = 0
        app._expire_hacoo_login()
        assert app.hacoo_email is None
        late = Event("late-password")
        await app.on_command(late)
        late.delete.assert_awaited_once()
        await app.on_command(Event("/hacoo_login"))
        await app.on_command(Event("/cancelar"))
        assert app.hacoo_step is None
        await app.on_command(Event("/hacoo_login"))
        await app.on_command(Event("owner@example.test", delete_error=True))
        app.hacoo.login.assert_not_called()
        assert app.hacoo_step is None
        await app.http.aclose()

    asyncio.run(run())
    app.store.db.close()


def test_errors_do_not_echo_credentials(tmp_path, monkeypatch, caplog):
    app = setup_app(tmp_path, monkeypatch)
    app.hacoo.login.side_effect = RuntimeError("password: top-secret")

    async def run():
        await app.on_command(Event("/hacoo_login"))
        await app.on_command(Event("owner@example.test"))
        event = Event("top-secret")
        await app.on_command(event)
        assert "top-secret" not in str(event.respond.call_args_list) + caplog.text
        await app.http.aclose()

    asyncio.run(run())
    app.store.db.close()


def test_conversion_pipeline_no_fallback_and_dedup(tmp_path, monkeypatch):
    app = setup_app(tmp_path, monkeypatch)
    app.store.set("hacoo_generator", True)
    app.hacoo.convert = AsyncMock(side_effect=LoginRequired())
    job = Job(0, 201, parse("Tenis\n25€", [SOURCE + "?f=OTHER"]), web=True, origin="https://t.me/canal/201")

    async def run():
        assert "Sessão Hacoo" in await app.process(job)
        assert app.store.count_pending() == 0  # Existing template is never used.
        app.hacoo.convert.side_effect = None
        app.hacoo.convert.return_value = GENERATED
        await asyncio.gather(app.process(job), app.process(job))
        assert app.store.count_pending() == 1
        assert app.hacoo.convert.await_count == 2  # Failure + one successful conversion.
        payload = app.store.pop_pending(1)
        assert payload["values"]["link"] == GENERATED
        app.store.set("paused", True)
        assert not await app.publish(payload)
        app.store.set("paused", False)
        app.store.set("web_sources", [])
        assert not await app.publish(payload)
        await app.http.aclose()

    asyncio.run(run())
    app.store.db.close()


def test_automatic_publication_uses_generated_link(tmp_path, monkeypatch):
    app = setup_app(tmp_path, monkeypatch)
    app.store.set("hacoo_generator", True)
    app.store.set("mode", "auto")
    app.hacoo.convert = AsyncMock(return_value=GENERATED)
    job = Job(0, 201, parse("Tenis\n25€", [SOURCE]), web=True, origin="https://t.me/canal/201")
    asyncio.run(app.process(job))
    assert app.store.recent()[0]["url"] == GENERATED
    assert GENERATED in app.bot.sent[-1][1]
    app.store.db.close()


def test_account_messages_require_authorized_source_and_topic(tmp_path, monkeypatch):
    app = setup_app(tmp_path, monkeypatch)
    app.store.add_source(-10055, "Allowed")
    app.store.set_topic(-10055, 10)
    message = SimpleNamespace(id=123, photo=True, raw_text="Tenis\n25€\n" + SOURCE,
                              entities=[], reply_markup=None,
                              reply_to=SimpleNamespace(forum_topic=True, reply_to_top_id=10))

    async def run():
        await app.on_source_message(SimpleNamespace(chat_id=-10066, message=message))
        assert app.queue.empty()
        message.reply_to.reply_to_top_id = 11
        await app.on_source_message(SimpleNamespace(chat_id=-10055, message=message))
        assert app.queue.empty()
        message.reply_to.reply_to_top_id = 10
        await app.on_source_message(SimpleNamespace(chat_id=-10055, message=message))
        assert app.queue.qsize() == 1
        app.store.remove_source(-10055)
        app.hacoo.convert = AsyncMock()
        await app.process(app.queue.get_nowait())
        app.hacoo.convert.assert_not_called()
        await app.http.aclose()

    asyncio.run(run())
    app.store.db.close()


# Browser fixture exercises the real adapter, with all network requests fulfilled
# locally. No real credentials or Telegram posts. Opt in after installing Chromium.
@pytest.mark.skipif(os.environ.get("HACOO_BROWSER_TESTS") != "1", reason="Set HACOO_BROWSER_TESTS=1 and install Chromium")
def test_browser_login_session_restore_conversion_and_logout(tmp_path):
    class FixtureGenerator(HacooGenerator):
        @asynccontextmanager
        async def _page(self, state=None):
            async with super()._page(state) as (page, context):
                async def route(request):
                    if request.request.url.endswith("/sign"):
                        body = '''<button>Sign In</button><div role="tabpanel">
                        <input placeholder="Email"><input type="password" placeholder="Password">
                        <button onclick="document.cookie='fixture=1;path=/';location.href='/en-GB/promotion/link'">Sign In</button></div>'''
                    else:
                        assert any(c["name"] == "fixture" for c in await context.cookies())
                        body = '''<textarea></textarea><button onclick="document.querySelector('[role=dialog]').hidden=false">Create Link</button>
                        <div role="dialog" hidden><textarea readonly>https://c.onlyaff.app/my-test-link</textarea></div>'''
                    await request.fulfill(status=200, content_type="text/html", body=body)
                await context.route("**/*", route)
                yield page, context

    async def run():
        adapter = FixtureGenerator(vault(tmp_path))
        try:
            await adapter.login(5, "fixture@example.test", "fixture-password")
            assert b"fixture-password" not in adapter.vault.path.read_bytes()
            assert await adapter.convert(5, SOURCE + "?f=OTHER") == GENERATED
            # A second operation starts a new context from the encrypted session.
            assert await adapter.convert(5, SOURCE) == GENERATED
            await adapter.logout()
            with pytest.raises(LoginRequired):
                await adapter.convert(5, SOURCE)
        finally:
            await adapter.close()
    asyncio.run(run())


@pytest.mark.skipif(os.environ.get("HACOO_BROWSER_TESTS") != "1", reason="Set HACOO_BROWSER_TESTS=1 and install Chromium")
@pytest.mark.parametrize("failure", ["login", "expired", "timeout", "invalid_result"])
def test_browser_failures_are_closed_and_sanitized(tmp_path, failure):
    class FailureGenerator(HacooGenerator):
        @asynccontextmanager
        async def _page(self, state=None):
            async with super()._page(state) as (page, context):
                page.set_default_timeout(400)

                async def route(request):
                    if failure == "login":
                        body = '''<div role="tabpanel"><input placeholder="Email">
                        <input type="password" placeholder="Password"><button>Sign In</button></div>'''
                    elif failure == "expired" and request.request.url.endswith("/promotion/link"):
                        await request.fulfill(status=302, headers={"Location": "/en-GB/join"})
                        return
                    elif failure == "expired":
                        body = "Please sign in"
                    elif failure == "timeout":
                        body = '<textarea></textarea><button>Create Link</button>'
                    else:
                        body = '''<textarea></textarea><button>Create Link</button>
                        <div role="dialog"><textarea readonly>https://evil.test/private-test-data</textarea></div>'''
                    await request.fulfill(status=200, content_type="text/html", body=body)

                await context.route("**/*", route)
                yield page, context

    async def run():
        adapter = FailureGenerator(vault(tmp_path))
        try:
            if failure == "login":
                with pytest.raises(HacooError) as exc:
                    await adapter.login(5, "private-test-email@example.test", "private-test-password")
                assert not adapter.vault.path.exists()
            else:
                adapter.vault.save(5, {"cookies": [], "origins": []})
                with pytest.raises(HacooError) as exc:
                    await adapter.convert(5, SOURCE)
                assert adapter.vault.path.exists() == (failure != "expired")
            assert "private-test" not in str(exc.value)
        finally:
            await adapter.close()

    asyncio.run(run())
