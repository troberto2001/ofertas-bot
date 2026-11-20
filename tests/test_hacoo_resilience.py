"""Resiliência do gerador Hacoo: repetição, autenticação automática, fases e avisos."""
import asyncio
import logging
from contextlib import asynccontextmanager

import pytest
from cryptography.fernet import Fernet

from app.hacoo import HacooError, HacooGenerator, LoginRequired, SessionVault, _Transient
from test_force import make_app

SOURCE = "https://www.hacoo.pl/p/1201"
GENERATED = "https://c.onlyaff.app/meu-link"
CREDS = ("dono@example.test", "pw-de-teste")


def vault(tmp_path):
    return SessionVault(tmp_path / "session.enc", Fernet.generate_key().decode())


class Fake(HacooGenerator):
    """Substitui as duas operações de browser por roteiros controlados."""
    def __init__(self, v, credentials=None, script=()):
        super().__init__(v, credentials)
        self.script = list(script)   # o que cada _convert_once faz: exceção ou texto
        self.convert_calls = 0
        self.logins = []
        self.closed = 0
        self.login_error = None

    async def _convert_once(self, owner, source, session):
        self.convert_calls += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    async def _login_locked(self, owner, email, password):
        self.logins.append((email, password))
        if self.login_error:
            raise self.login_error
        self.vault.save(owner, {"cookies": [], "origins": []})

    async def close(self):
        self.closed += 1


def run(coro):
    return asyncio.run(coro)


def test_transient_failure_is_retried_once_with_a_fresh_browser(tmp_path):
    g = Fake(vault(tmp_path), script=[_Transient("criar o link", "TimeoutError"), GENERATED])
    g.vault.save(5, {"cookies": [], "origins": []})
    assert run(g.convert(5, SOURCE)) == GENERATED
    assert g.convert_calls == 2 and g.closed == 1


def test_two_failures_report_the_phase_and_never_the_cause(tmp_path, caplog):
    g = Fake(vault(tmp_path), script=[_Transient("criar o link", "TimeoutError")] * 2)
    g.vault.save(5, {"cookies": [], "origins": []})
    with caplog.at_level(logging.INFO, logger="ofertas.hacoo"):
        with pytest.raises(HacooError) as exc:
            run(g.convert(5, SOURCE))
    msg = str(exc.value)
    assert "fase: criar o link" in msg and "Não publiquei" in msg
    assert "TimeoutError" in caplog.text and "criar o link" in caplog.text  # diagnóstico só no log
    assert g.vault.path.exists()  # falha transitória não apaga a sessão


def test_real_wrapper_hides_exception_message(tmp_path):
    class Boom(HacooGenerator):
        @asynccontextmanager
        async def _page(self, state=None):
            raise RuntimeError("private-secret-cookie")
            yield

    g = Boom(vault(tmp_path))
    with pytest.raises(_Transient) as exc:
        run(g._convert_once(5, SOURCE, {"state": {}, "created": 0}))
    assert exc.value.step == "abrir o gerador" and exc.value.kind == "RuntimeError"
    assert "private-secret" not in str(exc.value)


def test_auto_login_when_there_is_no_session(tmp_path):
    g = Fake(vault(tmp_path), CREDS, script=[GENERATED])  # sem sessão guardada
    assert run(g.convert(5, SOURCE)) == GENERATED
    assert g.logins == [CREDS]


def test_auto_login_when_site_rejects_session_then_retries(tmp_path):
    g = Fake(vault(tmp_path), CREDS, script=[LoginRequired(), GENERATED])
    g.vault.save(5, {"cookies": [], "origins": []})
    assert run(g.convert(5, SOURCE)) == GENERATED
    assert g.logins == [CREDS] and g.convert_calls == 2


def test_auto_login_is_attempted_only_once_per_conversion(tmp_path):
    g = Fake(vault(tmp_path), CREDS, script=[LoginRequired(), LoginRequired()])
    g.vault.save(5, {"cookies": [], "origins": []})
    with pytest.raises(LoginRequired):
        run(g.convert(5, SOURCE))
    assert len(g.logins) == 1  # sem ciclos de login


def test_failed_auto_login_surfaces_fixed_message_without_loop(tmp_path):
    g = Fake(vault(tmp_path), CREDS, script=[])
    g.login_error = HacooError("Login Hacoo não concluído.")
    with pytest.raises(HacooError):
        run(g.convert(5, SOURCE))
    assert len(g.logins) == 1


def test_without_credentials_behaviour_is_unchanged(tmp_path):
    g = Fake(vault(tmp_path), None, script=[LoginRequired()])
    g.vault.save(5, {"cookies": [], "origins": []})
    with pytest.raises(LoginRequired):
        run(g.convert(5, SOURCE))
    assert not g.vault.path.exists() and g.logins == [] and not g.auto_login
    g2 = Fake(vault(tmp_path / "x" if False else tmp_path), None, script=[])
    with pytest.raises(LoginRequired):  # nem sessão nem credenciais
        run(g2.convert(5, SOURCE))


def test_repeated_failure_notice_is_sent_once_per_30_minutes(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    text = "⚠️ Conversão Hacoo falhou. Não publiquei."

    async def go():
        for _ in range(5):
            assert await app._fail(False, text) == text
        await app._fail(False, "⚠️ Outro aviso diferente")
        await app._fail(True, text)  # modo forçado nunca notifica
        await app.http.aclose()

    asyncio.run(go())
    sent = [m for m in app.bot.sent if "Conversão Hacoo" in m[1]]
    assert len(sent) == 1
    assert any("Outro aviso" in m[1] for m in app.bot.sent)
    app.store.db.close()


def test_password_is_not_logged_or_in_repr(monkeypatch, tmp_path):
    from app.config import Config
    for k, v in dict(API_ID="1", API_HASH="x" * 32, BOT_TOKEN="1:x", OWNER_ID="5", DATA_DIR=str(tmp_path),
                     HACOO_EMAIL="dono@example.test", HACOO_PASSWORD="p#ss w0rd").items():
        monkeypatch.setenv(k, v)
    cfg = Config.load()
    assert cfg.hacoo_password == "p#ss w0rd" and "w0rd" not in repr(cfg) and "dono@" not in repr(cfg)
