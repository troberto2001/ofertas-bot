"""Hacoo's own generator in an isolated browser; no password persistence.

Selectors checked against the public en-GB site on 2026-10-27. Never log
Playwright exceptions: they can include input values, cookies or page content.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken

ORIGIN = "https://affiliate.hacoo.app"
GENERATOR = ORIGIN + "/en-GB/promotion/link"
SIGN_IN = ORIGIN + "/en-GB/sign"
PRODUCT_HOSTS = ("hacoo.app", "hacoo.pl", "hacoo.com")
RESULT_HOSTS = PRODUCT_HOSTS + ("onlyaff.app",)
INPUT = "textarea:not([readonly])"
OUTPUT = '[role="dialog"] textarea[readonly]'


class HacooError(Exception):
    """Only fixed, safe messages may cross this boundary."""


class LoginRequired(HacooError):
    def __init__(self):
        super().__init__("Sessão Hacoo ausente ou expirada. Usa /hacoo_login em privado.")


def allowed_url(url: str, hosts: tuple[str, ...]) -> bool:
    try:
        p = urlsplit(url)
        return (p.scheme == "https" and p.port in (None, 443)
                and not p.username and not p.password
                and not any(c.isspace() for c in url)
                and any(p.hostname == h or (p.hostname or "").endswith("." + h) for h in hosts))
    except ValueError:
        return False


def product_url(url: str) -> str:
    if not allowed_url(url, PRODUCT_HOSTS):
        raise HacooError("Domínio de produto Hacoo não autorizado.")
    p = urlsplit(url)
    match = re.search(r"/(?:detail|p)/(\d+)(?:/|$)", p.path)
    if not match:
        raise HacooError("O link Hacoo não contém um produto reconhecido.")
    # Strip the previous affiliate's parameters before asking Hacoo to generate.
    return f"https://{p.hostname}{p.path[:match.end(1)]}"


def result_url(value: str, source: str) -> str:
    value = value.strip()
    if not allowed_url(value, RESULT_HOSTS) or value == source:
        raise HacooError("O gerador Hacoo não devolveu um link de afiliado válido.")
    if allowed_url(value, PRODUCT_HOSTS):
        if product_url(value) != source or not urlsplit(value).query:
            raise HacooError("O gerador Hacoo devolveu um produto inesperado.")
    return value


class SessionVault:
    """Authenticated encryption, atomic writes, owner binding and bounded age."""
    def __init__(self, path: Path, key: str, ttl: int = 7 * 86400):
        self.path, self.ttl = path, ttl
        self.cipher = None
        if key:
            try:
                self.cipher = Fernet(key.encode("ascii"))
            except (ValueError, UnicodeError):
                raise HacooError("HACOO_SESSION_KEY inválida; gera uma chave Fernet.") from None

    def require_key(self):
        if self.cipher is None:
            raise HacooError("Configura HACOO_SESSION_KEY no servidor antes de /hacoo_login.")

    def load(self, owner: int) -> dict:
        self.require_key()
        try:
            data = json.loads(self.cipher.decrypt(self.path.read_bytes(), ttl=self.ttl))
            if data["owner"] != owner or data["version"] != 1:
                raise ValueError
            if time.time() - data["created"] > self.ttl:
                raise ValueError
            return data
        except (OSError, InvalidToken, ValueError, KeyError, TypeError):
            raise LoginRequired() from None

    def save(self, owner: int, state: dict, created: float | None = None):
        self.require_key()
        data = {"version": 1, "owner": owner, "created": created or time.time(), "state": state}
        encrypted = self.cipher.encrypt(json.dumps(data).encode())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".hacoo-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o600)
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def clear(self):
        self.path.unlink(missing_ok=True)


class HacooGenerator:
    def __init__(self, vault: SessionVault):
        self.vault = vault
        self.lock = asyncio.Lock()
        self._pw = self._browser = None

    @asynccontextmanager
    async def _page(self, state=None):
        from playwright.async_api import async_playwright

        if self._browser is None or not self._browser.is_connected():
            if self._pw is not None:
                await self._pw.stop()
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=True)
        context = await self._browser.new_context(storage_state=state, locale="en-GB")
        try:
            page = await context.new_page()
            page.set_default_timeout(15000)
            page.set_default_navigation_timeout(30000)
            yield page, context
        finally:
            await context.close()

    @staticmethod
    def _check_origin(page):
        if urlsplit(page.url).scheme != "https" or urlsplit(page.url).netloc != "affiliate.hacoo.app":
            raise HacooError("A página de autenticação Hacoo mudou de endereço.")

    async def _open_generator(self, page):
        await page.goto(GENERATOR, wait_until="domcontentloaded")
        try:
            await page.locator(INPUT).wait_for(state="visible")
        except Exception:
            if re.search(r"/(?:join|sign|verify)(?:[/?-]|$)", page.url):
                raise LoginRequired() from None
            raise HacooError("Gerador Hacoo indisponível ou conta sem acesso à promoção de produtos.") from None
        self._check_origin(page)

    async def login(self, owner: int, email: str, password: str):
        self.vault.require_key()
        async with self.lock:
            try:
                async with asyncio.timeout(90), self._page() as (page, context):
                    await page.goto(SIGN_IN, wait_until="domcontentloaded")
                    await page.get_by_placeholder("Email", exact=True).wait_for(state="visible")
                    self._check_origin(page)
                    await page.get_by_placeholder("Email", exact=True).fill(email)
                    await page.get_by_placeholder("Password", exact=True).fill(password)
                    await page.get_by_role("tabpanel").get_by_role("button", name="Sign In", exact=True).click()
                    await page.get_by_placeholder("Password", exact=True).wait_for(state="hidden")
                    await self._open_generator(page)
                    self.vault.save(owner, await context.storage_state())
            except HacooError:
                raise
            except Exception:
                raise HacooError("Login Hacoo não concluído. Verifica as credenciais e o acesso ao programa; se houver verificação adicional, conclui-a no site.") from None

    async def convert(self, owner: int, url: str) -> str:
        source = product_url(url)
        async with self.lock:
            session = self.vault.load(owner)
            try:
                async with asyncio.timeout(75), self._page(session["state"]) as (page, context):
                    await self._open_generator(page)
                    await page.locator(INPUT).fill(source)
                    await page.get_by_role("button", name="Create Link", exact=True).click()
                    output = page.locator(OUTPUT)
                    await output.wait_for(state="visible")
                    result = result_url(await output.input_value(), source)
                    self.vault.save(owner, await context.storage_state(), session["created"])
                    return result
            except LoginRequired:
                self.vault.clear()
                raise
            except HacooError:
                raise
            except Exception:
                raise HacooError("Conversão Hacoo falhou ou excedeu o tempo limite. Não publiquei; verifica /hacoo_estado ou volta a usar /hacoo_login.") from None

    async def logout(self):
        async with self.lock:
            self.vault.clear()
            await self.close()

    async def close(self):
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
