"""Bot de ofertas: lê ofertas, converte o link para o teu e publica no teu canal.

Dois modos (variável MODE no .env):

  web      (por omissão) SEM conta. Lê canais PÚBLICOS pela pré-visualização web
           (t.me/s/canal) e publica com o bot, que tem de ser administrador do
           teu canal de destino. Ninguém no canal de origem dá por nada.
           Sem emojis premium (um bot normal não os pode enviar).

  account  Usa a tua conta (userbot) para ler grupos/canais, mesmo privados, e
           publicar (com emojis premium, se a conta tiver Premium).

Em ambos há um bot de controlo (BotFather) que só responde a ti.
"""
from __future__ import annotations

import asyncio
import html
import io
import logging
import random
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime

from telethon import Button, TelegramClient, errors, events, utils
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import MessageEntityCustomEmoji

from . import links, render, webfeed
from .config import Config
from .channel_fill import FillRun, interleave
from .hacoo import HacooError, HacooGenerator, SessionVault
from .links import UA
from .parser import Offer, find_urls, parse
from .store import Store
from .title_translation import translate_product_title

log = logging.getLogger("ofertas")

AJUDA = """<b>Comandos</b>

<b>Estado</b>
/estado – resumo do bot
/ultimas – últimas publicações
/exemplo – FORÇAR: mostra as 5 ofertas mais recentes da origem (ou /exemplo 10)
/teste – igual, mas SEM link (para testar formato, imagem e permissões do canal)
/encher – publicar 50 ofertas, fontes intercaladas e aleatórias, com tradução
/encher_estado · /encher_parar – consultar ou interromper o enchimento
/pausar · /retomar – parar/continuar

<b>Publicação</b>
/manual – cada oferta chega aqui para aprovares
/auto – publica sozinho
/codigo CODIGO – muda o teu código de desconto
/modelo – ver/mudar o modelo da mensagem
/traducao · /traducao ligar · /traducao desligar – traduzir o produto ES → PT
/linkmodelo LINK – cola um link teu da loja (gerado na tua conta)
/hacoo_login – ligar o gerador Hacoo (email e palavra-passe, só em privado)
/hacoo_estado · /hacoo_sair – ver sessão / apagar sessão local
/hacoo_modelo – voltar explicitamente ao método /linkmodelo
/cancelar – cancelar o pedido de credenciais
/limite N – máximo de publicações por hora

<b>Origem e destino</b>
/adicionar t.me/canal – canal público a copiar (modo web) ou grupo (modo conta)
/fontes – ver fontes · /remover – tirar uma fonte
/destino @canal – canal onde publicar (o bot tem de ser administrador)
/recarregar – recarregar o ficheiro rules.json

<i>Só modo conta:</i> /escolher (botões), /topico ID_FONTE ID_TOPICO"""


@dataclass
class Job:
    chat_id: int
    msg_id: int
    offer: Offer
    photo_url: str | None = None
    web: bool = False
    origin: str = ""


def msg_link(chat_id: int, msg_id: int) -> str:
    s = str(chat_id)
    if s.startswith("-100"):
        return f"https://t.me/c/{s[4:]}/{msg_id}"
    return f"(grupo {chat_id}, mensagem {msg_id})"


def collect_urls(m) -> list[str]:
    urls: list[str] = []
    for e in m.entities or []:
        u = getattr(e, "url", None)
        if u:
            urls.append(u)
    markup = getattr(m, "reply_markup", None)
    for row in getattr(markup, "rows", None) or []:
        for b in getattr(row, "buttons", None) or []:
            u = getattr(b, "url", None)
            if u:
                urls.append(u)
    return find_urls(m.raw_text or "", urls)


def topic_of(m) -> int:
    """ID do tópico de um fórum (1 = geral)."""
    rt = getattr(m, "reply_to", None)
    if rt and getattr(rt, "forum_topic", False):
        return rt.reply_to_top_id or rt.reply_to_msg_id or 1
    return 1


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.web = cfg.mode == "web"
        self.store = Store(cfg.db_path)
        self.client = None if self.web else TelegramClient(cfg.user_session, cfg.api_id, cfg.api_hash)
        self.bot = TelegramClient(cfg.bot_session, cfg.api_id, cfg.api_hash)
        self.http = links.make_client()
        self.queue: asyncio.Queue[Job] = asyncio.Queue()
        self.owner: int = cfg.owner_id or 0
        self.premium = False
        self.awaiting: str | None = None
        self.idle_alerted = False
        self.warned_template = False
        self.web_fail: dict[str, int] = {}
        self.started = time.time()
        self.hacoo = HacooGenerator(SessionVault(cfg.data_dir / "hacoo-session.enc", cfg.hacoo_session_key))
        self.hacoo_step = None
        self.hacoo_email = None
        self.hacoo_deadline = 0.0
        self.hacoo_timer = None
        self.command_lock = asyncio.Lock()
        self.process_lock = asyncio.Lock()
        self.publish_lock = asyncio.Lock()
        self.fill_run: FillRun | None = None
        self.fill_task: asyncio.Task | None = None

    # ---------- definições ----------
    def s(self, key: str, default=None):
        return self.store.get(key, default)

    @property
    def template(self) -> str:
        return self.s("template", render.DEFAULT_TEMPLATE)

    @property
    def code(self) -> str:
        return self.s("code", self.cfg.default_code)

    @property
    def mode(self) -> str:
        return self.s("mode", "manual")

    @property
    def paused(self) -> bool:
        return bool(self.s("paused", False))

    @property
    def max_per_hour(self) -> int:
        return int(self.s("max_per_hour", self.cfg.max_per_hour))

    @property
    def web_sources(self) -> list[str]:
        return list(self.s("web_sources", []))

    def n_sources(self) -> int:
        return len(self.web_sources) if self.web else len(self.store.list_sources())

    def product_title(self, title: str) -> str:
        return translate_product_title(title) if self.s("translate_titles", True) else title

    # ---------- avisos ----------
    async def notify(self, text: str, **kw) -> None:
        try:
            await self.bot.send_message(self.owner, text, parse_mode="html", **kw)
        except Exception as e:  # o dono ainda não abriu o bot, etc.
            log.warning("Aviso via bot falhou: %s", e)
            if self.client:
                try:
                    await self.client.send_message("me", re.sub(r"<[^>]+>", "", text))
                except Exception:
                    log.exception("Não consegui avisar o dono")

    # ---------- arranque ----------
    async def start(self) -> None:
        tasks = []
        if not self.web:
            await self.client.connect()
            if not await self.client.is_user_authorized():
                raise SystemExit("Conta não autenticada. Corre: botctl login")
            me = await self.client.get_me()
            if getattr(me, "bot", False):
                raise SystemExit(
                    "A sessão está ligada a um BOT (usaste o token no login). "
                    "Apaga data/user.session e faz 'botctl login' com um número de telemóvel."
                )
            self.premium = bool(getattr(me, "premium", False))
            if not self.owner:
                self.owner = me.id
            self.client.add_event_handler(self.on_source_message, events.NewMessage())
            tasks += [self.client.run_until_disconnected()]
        else:
            tasks += [self.web_poller()]

        await self.bot.start(bot_token=self.cfg.bot_token)
        links.load_rules(self.cfg.rules_path)  # cria o ficheiro por omissão
        self.bot.add_event_handler(
            self.on_command, events.NewMessage(from_users=self.owner, incoming=True)
        )
        self.bot.add_event_handler(self.on_button, events.CallbackQuery())

        log.info("Pronto. modo=%s dono=%s premium=%s aprovacao=%s", self.cfg.mode, self.owner, self.premium, self.mode)
        extra = (
            "🌐 Modo sem conta (canais públicos pela web)."
            if self.web
            else ("✅ Telegram Premium ativo." if self.premium else "ℹ️ Conta sem Premium: emojis premium viram emojis normais.")
        )
        await self.notify(f"🤖 Bot iniciado.\n{extra}\nEnvia /estado ou /ajuda.")
        tasks += [self.worker(), self.idle_monitor(), self.bot.run_until_disconnected()]
        try:
            await asyncio.gather(*tasks)
        finally:
            self._cancel_hacoo_login()
            if self.fill_task and not self.fill_task.done():
                self.fill_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.fill_task
            await self.hacoo.close()
            await self.http.aclose()

    # ---------- leitura: modo conta ----------
    async def on_source_message(self, event) -> None:
        try:
            if self.paused:
                return
            if event.chat_id == self.s("dest"):
                return  # nunca ler o próprio canal de destino
            src = self.store.get_source(event.chat_id)
            if not src or not src.active:
                return
            m = event.message
            if src.topic_id and topic_of(m) != src.topic_id:
                return
            if self.cfg.require_photo and not m.photo:
                return
            offer = parse(m.raw_text or "", collect_urls(m))
            if not offer:
                return
            await self.queue.put(Job(event.chat_id, m.id, offer))
        except Exception:
            log.exception("Erro a ler mensagem da fonte")

    # ---------- leitura: modo web ----------
    async def poll_channel(self, name: str) -> None:
        posts = await webfeed.fetch(self.http, name)
        key = f"web_last:{name}"
        last = self.s(key)
        top = max(p.id for p in posts)
        if last is None:  # primeira vez: marca o ponto de partida, não publica o histórico
            self.store.set(key, top)
            return
        new = sorted((p for p in posts if p.id > last), key=lambda p: p.id)
        if new:
            self.store.set(key, top)
        for p in new:
            if self.cfg.require_photo and not p.photo_url:
                continue
            offer = parse(p.text, p.urls)
            if not offer:
                continue
            await self.queue.put(
                Job(0, p.id, offer, photo_url=p.photo_url, web=True, origin=f"https://t.me/{name}/{p.id}")
            )

    async def web_poller(self) -> None:
        while True:
            if not self.paused:
                for name in self.web_sources:
                    try:
                        await self.poll_channel(name)
                        self.web_fail[name] = 0
                    except Exception as e:
                        n = self.web_fail.get(name, 0) + 1
                        self.web_fail[name] = n
                        log.warning("Falhou a ler %s (%s): %s", name, n, e)
                        if n == 5:
                            await self.notify(
                                f"⚠️ Não consigo ler o canal <b>{html.escape(name)}</b> há várias tentativas: "
                                f"{html.escape(str(e))}"
                            )
                    await asyncio.sleep(random.uniform(1, 4))
            await asyncio.sleep(self.cfg.web_poll_sec + random.uniform(0, 15))

    # ---------- processamento ----------
    async def worker(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                await self.process(job)
            except Exception as e:
                log.exception("Erro a processar oferta")
                await self.notify(f"⚠️ Erro a processar uma oferta: {html.escape(str(e))}")
            finally:
                self.queue.task_done()

    async def _preview_nolink(self, job: Job) -> None:
        """Teste: a oferta tal como ficaria, mas sem a linha do link (não converte nada).
        Publicável pelo botão; não fica no histórico nem conta para os limites."""
        origem = job.origin or msg_link(job.chat_id, job.msg_id)
        values = {
            "titulo": self.product_title(job.offer.title),
            "preco": job.offer.price,
            "codigo": self.code or None,
            "link": None,  # as linhas com campos vazios são omitidas no modelo
        }
        payload = {
            "chat_id": job.chat_id, "msg_id": job.msg_id, "values": values,
            "key": f"teste:{job.chat_id}:{job.msg_id}",
            "web": job.web, "photo_url": job.photo_url, "origin": origem, "test": True,
        }
        await self.send_for_approval(payload, note="🧪 TESTE (sem link)\n\n")

    async def _fail(self, force: bool, text: str) -> str:
        """Em modo normal avisa o dono; em modo forçado devolve o texto para o resumo."""
        if not force:
            await self.notify(text)
        return text

    async def process(self, job: Job, force: bool = False, nolink: bool = False, *, fill: FillRun | None = None) -> str | None:
        async with self.process_lock:
            return await self._process(job, force, nolink, fill=fill)

    async def _process(self, job: Job, force: bool = False, nolink: bool = False, *, fill: FillRun | None = None) -> str | None:
        """Converte e publica/envia para aprovação. Devolve o motivo se não avançou.
        force=True: ignora o anti-duplicados e envia sempre para aprovação manual.
        nolink=True (teste): não converte o link; mostra a oferta sem a linha do link."""
        if fill is not None and (not self._fill_allowed(fill) or not self.source_allowed(job.web, job.chat_id, job.origin)):
            return "Enchimento interrompido ou fonte já não autorizada."
        if not force and (self.paused or not self.source_allowed(job.web, job.chat_id, job.origin)):
            return None
        if nolink:
            await self._preview_nolink(job)
            return None
        rules = links.load_rules(self.cfg.rules_path)
        rule = final = None
        for u in job.offer.urls:
            try:
                res = await links.resolve(u, rules, self.http)
            except Exception as e:
                log.warning("Falhou a resolver %s: %s", u, e)
                continue
            r = links.match_rule(res, rules)
            if r:
                rule, final = r, res
                break
        origem = job.origin or msg_link(job.chat_id, job.msg_id)
        if not rule:
            return await self._fail(
                force or fill is not None,
                "⚠️ Loja não suportada ou link que não consegui seguir. <b>Não publiquei.</b>\n"
                f"Links: {html.escape(' '.join(job.offer.urls))}\nOrigem: {origem}",
            )
        pid = links.extract_id(rule, final)
        if not pid:
            return await self._fail(
                force or fill is not None,
                f"⚠️ O formato do link da loja <b>{rule.name}</b> mudou (não encontro o ID do produto). "
                f"<b>Não publiquei.</b>\n{html.escape(final)}\nOrigem: {origem}",
            )
        key = f"{rule.name}:{pid}"
        if fill is not None and key in fill.seen:
            return "Produto repetido nesta execução."
        if fill is None and not force and (self.store.was_posted(key, self.cfg.dedup_hours) or self.store.has_pending_key(key)):
            return None
        if rule.name == "hacoo" and self.s("hacoo_generator", False):
            try:
                link = await self.hacoo.convert(self.owner, final)
            except HacooError as exc:
                return await self._fail(force or fill is not None, "⚠️ " + html.escape(str(exc)) + " Não publiquei.")
        else:
            link = links.build_link(rule, pid, final)
        if not link:
            msg = (
                "⚠️ Ainda não tenho o teu link de afiliado da loja "
                f"<b>{rule.name}</b>. Gera um link de um produto na tua conta e envia:\n"
                "<code>/linkmodelo SEU_LINK</code>\nAté lá não publico nada."
            )
            if fill is None and not force and not self.warned_template:
                self.warned_template = True
                await self.notify(msg)
            return msg

        key = f"{rule.name}:{pid}"
        note = ""
        if fill is not None:
            pass  # Explicit batch can reuse history, but not a product already in this run.
        elif force:
            if self.store.was_posted(key, self.cfg.dedup_hours):
                note = f"⚠️ Já publicada nas últimas {self.cfg.dedup_hours}h\n\n"
        elif self.store.was_posted(key, self.cfg.dedup_hours) or self.store.has_pending_key(key):
            log.info("Duplicado ignorado: %s", key)
            return None

        values = {
            "titulo": translate_product_title(job.offer.title) if fill is not None else self.product_title(job.offer.title),
            "preco": job.offer.price,
            "codigo": self.code or None,
            "link": link,
        }
        payload = {
            "chat_id": job.chat_id, "msg_id": job.msg_id, "values": values, "key": key,
            "web": job.web, "photo_url": job.photo_url, "origin": origem,
        }
        if fill is not None:
            if not await self.publish(payload, fill=fill):
                return "Oferta não publicada."
            fill.seen.add(key)
            fill.published += 1
        elif force:
            await self.send_for_approval(payload, note=note)
        elif self.mode == "auto":
            await self.publish(payload)
        else:
            await self.send_for_approval(payload)
        return None

    # ---------- imagens ----------
    async def _download(self, url: str) -> io.BytesIO | None:
        try:
            r = await self.http.get(url, headers={"User-Agent": UA}, follow_redirects=True)
            r.raise_for_status()
        except Exception as e:
            log.warning("Falhou a descarregar a imagem: %s", e)
            return None
        f = io.BytesIO(r.content)
        f.name = "oferta.jpg"
        return f

    # ---------- aprovação manual ----------
    async def send_for_approval(self, payload: dict, note: str = "") -> None:
        pid = self.store.add_pending(payload["key"], payload)
        text, _ = render.render(self.template, payload["values"], premium=False)
        text = note + render.to_plain(text)
        buttons = [[Button.inline("✅ Publicar", f"ok:{pid}"), Button.inline("❌ Rejeitar", f"no:{pid}")]]
        try:
            if payload.get("web"):
                f = await self._download(payload["photo_url"]) if payload.get("photo_url") else None
                if f:
                    await self.bot.send_file(self.owner, f, caption=text[:1000], buttons=buttons)
                else:
                    await self.bot.send_message(self.owner, text, buttons=buttons, link_preview=False)
            else:
                m = await self.client.get_messages(payload["chat_id"], ids=payload["msg_id"])
                if m and m.photo:
                    data = await self.client.download_media(m, file=bytes)
                    f = io.BytesIO(data)
                    f.name = "oferta.jpg"
                    await self.bot.send_file(self.owner, f, caption=text[:1000], buttons=buttons)
                else:
                    await self.bot.send_message(self.owner, text, buttons=buttons, link_preview=False)
        except Exception as e:
            log.warning("Pré-visualização falhou: %s", e)
            await self.notify(
                "⚠️ Tenho uma oferta por aprovar mas não consegui enviar-ta. "
                "Abre este bot e envia /start."
            )

    # ---------- publicar ----------
    def source_allowed(self, web: bool, chat_id: int, origin: str) -> bool:
        if web:
            return any(origin.startswith(f"https://t.me/{name}/") for name in self.web_sources)
        src = self.store.get_source(chat_id)
        return bool(src and src.active and chat_id != self.s("dest"))

    def _fill_allowed(self, run: FillRun) -> bool:
        if self.paused or self.s("dest") != run.destination:
            run.stop.set()
        return not run.stop.is_set()

    async def _publication_wait(self, seconds: float, fill: FillRun | None = None) -> bool:
        if fill is None:
            await asyncio.sleep(max(0, seconds))
            return not self.paused
        if not self._fill_allowed(fill):
            return False
        try:
            await asyncio.wait_for(fill.stop.wait(), timeout=max(0, seconds))
        except asyncio.TimeoutError:
            pass
        return self._fill_allowed(fill)

    async def _throttle(self, fill: FillRun | None = None) -> bool:
        while self.store.count_since(3600) >= self.max_per_hour:
            if not await self._publication_wait(30, fill):
                return False
        gap = time.time() - (self.store.last_post_ts() or 0)
        if gap < self.cfg.min_interval:
            if not await self._publication_wait(self.cfg.min_interval - gap, fill):
                return False
        return True

    async def publish(self, payload: dict, *, fill: FillRun | None = None) -> bool:
        async with self.publish_lock:
            return await self._publish(payload, fill=fill)

    async def _publish(self, payload: dict, *, fill: FillRun | None = None) -> bool:
        if fill is not None and not self._fill_allowed(fill):
            return False
        if self.paused or not self.source_allowed(bool(payload.get("web")), payload["chat_id"], payload.get("origin", "")):
            await self.notify("Publicação cancelada: bot em pausa ou fonte já não autorizada.")
            return False
        dest = self.s("dest")
        if not dest:
            await self.notify("⚠️ Falta escolher o canal de destino: /destino @canal")
            return False
        web = bool(payload.get("web"))
        photo = None
        m = None
        if web:
            if payload.get("photo_url"):
                photo = await self._download(payload["photo_url"])
                if photo is None and self.cfg.require_photo:
                    await self.notify("⚠️ Não consegui descarregar a imagem. Não publiquei.\n" + payload.get("origin", ""))
                    return False
        else:
            m = await self.client.get_messages(payload["chat_id"], ids=payload["msg_id"])
            if not m:
                await self.notify("⚠️ A mensagem original já não existe. Não publiquei.")
                return False
        if not payload.get("test"):
            if not await self._throttle(fill):
                return False
        # Recheck after sleeping: pause/removal can happen while queued.
        if self.paused or dest != self.s("dest") or not self.source_allowed(web, payload["chat_id"], payload.get("origin", "")):
            return False
        premium = False if web else self.premium
        text, spec = render.render(self.template, payload["values"], premium=premium)
        ents = [MessageEntityCustomEmoji(o, l, d) for o, l, d in spec] or None
        sender = self.bot if web else self.client
        for attempt in (1, 2):
            if (self.paused or dest != self.s("dest")
                    or not self.source_allowed(web, payload["chat_id"], payload.get("origin", ""))
                    or (fill is not None and not self._fill_allowed(fill))):
                return False
            try:
                if web:
                    if photo:
                        photo.seek(0)
                        await sender.send_file(dest, photo, caption=text, parse_mode=None)
                    else:
                        await sender.send_message(dest, text, parse_mode=None, link_preview=False)
                elif m.photo:
                    await sender.send_file(dest, m.photo, caption=text, formatting_entities=ents, parse_mode=None)
                else:
                    await sender.send_message(dest, text, formatting_entities=ents, parse_mode=None, link_preview=False)
                break
            except errors.FloodWaitError as e:
                if attempt == 2:
                    await self.notify(f"⚠️ Telegram pediu para esperar {e.seconds}s. Não publiquei.")
                    return False
                if not await self._publication_wait(e.seconds + 1, fill):
                    return False
            except Exception as e:
                log.exception("Falhou a publicar")
                await self.notify(
                    f"⚠️ Falhou a publicação: {html.escape(str(e))}\n"
                    "Confirma que o bot é administrador do canal de destino com permissão para publicar."
                )
                return False
        if not payload.get("test"):  # os testes não entram no histórico nem nos limites
            self.store.mark_posted(
                payload["key"], payload.get("chat_id") or 0, payload.get("msg_id") or 0,
                payload["values"].get("titulo") or "", payload["values"].get("link") or "",
            )
            self.idle_alerted = False
        log.info("Publicado %s", payload["key"])
        return True

    # ---------- alerta de inatividade ----------
    async def idle_monitor(self) -> None:
        while True:
            await asyncio.sleep(1800)
            try:
                if self.paused or self.idle_alerted or not self.n_sources():
                    continue
                last = self.store.last_post_ts() or self.started
                if time.time() - last > self.cfg.idle_alert_hours * 3600:
                    self.idle_alerted = True
                    await self.notify(
                        f"⏰ Há mais de {self.cfg.idle_alert_hours}h sem publicar nada. "
                        "Pode ser normal, ou a origem mudou. Vê /estado."
                    )
            except Exception:
                log.exception("idle_monitor")

    # ---------- comandos ----------
    async def on_command(self, event) -> None:
        # Check here too: callers and future handlers must not bypass privacy.
        if event.sender_id != self.owner or not event.is_private:
            return
        async with self.command_lock:
            if self.hacoo_step:
                await self._receive_hacoo_credential(event)
                return
            await self._dispatch_command(event)

    async def _dispatch_command(self, event) -> None:
        text = (event.raw_text or "").strip()
        try:
            if text.startswith("/"):
                self.awaiting = None
                cmd, _, arg = text.partition(" ")
                cmd = cmd.split("@")[0].lower()
                handler = getattr(self, "cmd_" + cmd[1:], None)
                if handler:
                    await handler(event, arg.strip())
                else:
                    await event.respond("Comando desconhecido. /ajuda")
            elif self.awaiting == "modelo":
                await self.set_template(event)
            elif self.awaiting == "destino":
                await self.set_dest_from_message(event)
        except Exception as e:
            log.exception("Erro num comando")
            await event.respond(f"⚠️ Erro: {e}")

    def _cancel_hacoo_login(self):
        if self.hacoo_timer:
            self.hacoo_timer.cancel()
        self.hacoo_timer = self.hacoo_step = self.hacoo_email = None
        self.hacoo_deadline = 0.0

    async def cmd_cancelar(self, event, arg):
        self._cancel_hacoo_login()
        self.awaiting = None
        await event.respond("Pedido cancelado.")

    async def cmd_hacoo_login(self, event, arg):
        if arg:
            await event.delete()
            await event.respond("Não coloques credenciais no comando. Envia apenas /hacoo_login.")
            return
        try:
            self.hacoo.vault.require_key()
        except HacooError as exc:
            await event.respond(str(exc))
            return
        self._cancel_hacoo_login()
        self.hacoo_step = "email"
        self.hacoo_deadline = time.monotonic() + 180
        self.hacoo_timer = asyncio.get_running_loop().call_later(180, self._expire_hacoo_login)
        await event.respond(
            "Envia o email da tua conta Hacoo neste chat privado. Tens 3 minutos para concluir. "
            "O bot tentará apagar as mensagens com credenciais antes de as usar; "
            "os chats com bots não têm encriptação ponta a ponta. "
            "A palavra-passe não será guardada. /cancelar interrompe o pedido."
        )

    def _expire_hacoo_login(self):
        # Keep a sentinel so a late password is deleted, never treated as a command.
        self.hacoo_email = None
        self.hacoo_step = "expired"
        self.hacoo_timer = None

    async def _receive_hacoo_credential(self, event):
        # No exceptions with credential-bearing objects may reach the generic logger.
        try:
            await event.delete()
        except Exception:
            self._cancel_hacoo_login()
            await event.respond("Não consegui apagar a mensagem com credenciais. Apaga-a manualmente. Login cancelado; não usei as credenciais.")
            return
        raw = event.raw_text or ""
        if raw.strip() == "/cancelar":
            self._cancel_hacoo_login()
            await event.respond("Pedido cancelado.")
            return
        if time.monotonic() > self.hacoo_deadline:
            self._cancel_hacoo_login()
            await event.respond("Pedido expirado. Recomeça com /hacoo_login.")
            return
        if getattr(event.message, "fwd_from", None):
            self._cancel_hacoo_login()
            await event.respond("Não aceito credenciais reencaminhadas. Recomeça com /hacoo_login.")
            return
        if self.hacoo_step == "email":
            if len(raw) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", raw.strip()):
                await event.respond("Email inválido. Envia novamente o email, ou /cancelar.")
                return
            self.hacoo_email, self.hacoo_step = raw.strip(), "password"
            await event.respond("Envia agora a palavra-passe Hacoo (ou /cancelar).")
            return
        email = self.hacoo_email
        self._cancel_hacoo_login()
        if not raw or len(raw) > 1024:
            await event.respond("Palavra-passe vazia ou demasiado longa. Recomeça com /hacoo_login.")
            return
        # Once chosen, never fall back silently to an old affiliate template.
        self.store.set("hacoo_generator", True)
        try:
            await self.hacoo.login(self.owner, email, raw)
        except HacooError as exc:
            await event.respond(str(exc))
        except Exception:
            await event.respond("Não foi possível guardar a sessão Hacoo. Verifica a configuração do servidor.")
        else:
            await event.respond("✅ Sessão Hacoo guardada cifrada. Gerador ativo; usa /exemplo para rever uma oferta antes de /auto.")
        finally:
            raw = email = None

    async def cmd_hacoo_estado(self, event, arg):
        mode = "gerador" if self.s("hacoo_generator", False) else "modelo"
        try:
            self.hacoo.vault.load(self.owner)
            status = "sessão local disponível (validada no site na próxima conversão)"
        except HacooError as exc:
            status = str(exc)
        await event.respond(f"Hacoo: {mode}. {status}")

    async def cmd_hacoo_sair(self, event, arg):
        await self.hacoo.logout()
        await event.respond("Sessão Hacoo apagada deste servidor. O modo gerador mantém-se: não converto até novo /hacoo_login. Para revogar outras sessões, usa o site Hacoo.")

    async def cmd_hacoo_modelo(self, event, arg):
        self.store.set("hacoo_generator", False)
        await event.respond("Método de modelo selecionado explicitamente. Configura /linkmodelo com um link da tua conta.")

    @staticmethod
    def main_buttons():
        return [
            [Button.inline("🔄 Forçar: mostrar as 5 últimas", "f:5")],
            [Button.inline("🧪 Forçar sem link (teste)", "g:5")],
        ]

    async def cmd_start(self, event, arg):
        await event.respond(AJUDA, parse_mode="html", buttons=self.main_buttons())

    cmd_ajuda = cmd_start

    async def cmd_traducao(self, event, arg):
        choice = arg.strip().casefold()
        if choice in ("ligar", "on"):
            self.store.set("translate_titles", True)
        elif choice in ("desligar", "off"):
            self.store.set("translate_titles", False)
        elif choice:
            await event.respond("Uso: /traducao, /traducao ligar ou /traducao desligar.")
            return
        enabled = self.s("translate_titles", True)
        await event.respond(
            f"Tradução do produto ES → PT: {'ligada' if enabled else 'desligada'}. "
            "Traduz nomes de produto e frases descritivas reconhecidas, incluindo depois da marca. Mantém marcas, modelos e referências. "
            "Aplica-se às próximas ofertas preparadas. Usa /exemplo para testar."
        )

    async def cmd_estado(self, event, arg):
        dest = self.s("dest_title") or ("não definido" if not self.s("dest") else str(self.s("dest")))
        rules = links.load_rules(self.cfg.rules_path)
        sem_modelo = [r.name for r in rules if not r.template and not (r.name == "hacoo" and self.s("hacoo_generator", False))]
        last = self.store.last_post_ts()
        ultima = datetime.fromtimestamp(last).strftime("%d/%m %H:%M") if last else "—"
        lines = [
            "<b>Estado</b>",
            f"{'⏸ Em pausa' if self.paused else '▶️ A correr'} · aprovação <b>{'automática' if self.mode == 'auto' else 'manual (aprovas tu)'}</b>",
            f"Modo: <b>{'sem conta (web)' if self.web else 'com conta'}</b> · Fontes: {self.n_sources()} · Destino: {html.escape(dest)}",
            f"Código: <code>{html.escape(self.code or '(vazio)')}</code>",
            (f"Verificação do canal a cada ~{self.cfg.web_poll_sec}s" if self.web else f"Premium: {'sim' if self.premium else 'não'}"),
            f"Últimas 24h: {self.store.count_since(86400)} publicações · última: {ultima}",
            f"Por aprovar: {self.store.count_pending()} · limite/hora: {self.max_per_hour}",
            "Hacoo: " + ("gerador (/hacoo_estado)" if self.s("hacoo_generator", False) else "modelo (/hacoo_login para ligar o gerador)"),
            "Tradução do produto ES → PT: " + ("ligada" if self.s("translate_titles", True) else "desligada") + " (/traducao)",
        ]
        if sem_modelo:
            lines.append(f"⚠️ Falta o link modelo de: {', '.join(sem_modelo)} (/linkmodelo)")
        if self.fill_run:
            lines.append(f"Enchimento: {self.fill_run.published}/{self.fill_run.target} · {self.fill_run.phase}")
        await event.respond("\n".join(lines), parse_mode="html", buttons=self.main_buttons())

    async def cmd_exemplo(self, event, arg):
        n = int(arg) if arg.isdigit() else 5
        await event.respond("🔄 A obter as últimas ofertas…")
        await self.force_recent(n)

    cmd_forcar = cmd_exemplo

    async def cmd_teste(self, event, arg):
        n = int(arg) if arg.isdigit() else 5
        await event.respond("🧪 A obter as últimas ofertas (sem link)…")
        await self.force_recent(n, nolink=True)

    async def cmd_encher(self, event, arg):
        if arg and arg != "50":
            await event.respond("Uso: /encher (50 publicações por execução).")
            return
        if self.fill_task and not self.fill_task.done():
            await event.respond("Já há um enchimento em curso. Usa /encher_estado ou /encher_parar.")
            return
        if self.paused:
            await event.respond("O bot está em pausa. Usa /retomar antes de /encher.")
            return
        if not self.s("dest") or not self.n_sources():
            await event.respond("Configura primeiro /destino e as fontes (/adicionar ou /escolher).")
            return
        if self.s("hacoo_generator", False):
            try:
                self.hacoo.vault.load(self.owner)
            except HacooError as exc:
                await event.respond(str(exc))
                return
        run = FillRun(destination=self.s("dest"))
        self.fill_run = run
        await event.respond(
            "▶️ Vou publicar 50 ofertas diretamente no destino atual, com tradução e links convertidos. "
            "Misturo as fontes e posso reutilizar ofertas antigas, sem repetir produtos neste lote. "
            f"Respeito o limite de {self.max_per_hour}/hora e o intervalo de {self.cfg.min_interval}s. "
            "Consulta /encher_estado; /encher_parar interrompe."
        )
        # Do not hold the command lock while fetching/converting/throttling.
        self.fill_task = asyncio.create_task(self._run_fill(run), name="encher-canal")

    async def cmd_encher_estado(self, event, arg):
        run = self.fill_run
        if run is None:
            await event.respond("Ainda não há uma execução nesta sessão. Usa /encher.")
            return
        await event.respond(
            f"Enchimento: {run.phase}. Publicadas: {run.published}/{run.target}. "
            f"Candidatas tentadas: {run.attempted}; não publicadas: {run.skipped}; "
            f"falhas na leitura de fontes: {run.source_errors}. "
            f"Limite atual: {self.max_per_hour}/hora."
        )

    async def cmd_encher_parar(self, event, arg):
        if not self.fill_task or self.fill_task.done():
            await event.respond("Não há enchimento em curso.")
            return
        self.fill_run.stop.set()
        await event.respond("Paragem pedida. Termino a operação em curso e não inicio outra publicação do lote.")

    async def _fill_candidates(self, run: FillRun) -> dict[str, list[Job]]:
        """Bounded history scan, without moving live poller cursors or pending items."""
        candidates: dict[str, list[Job]] = {}
        if self.web:
            # Resolve the destination to avoid filling from our own public channel.
            dest = await self.bot.get_entity(run.destination)
            destination_name = (getattr(dest, "username", None) or "").casefold()
            names = list(dict.fromkeys(self.web_sources))
            random.shuffle(names)
            for name in names:
                if not self._fill_allowed(run):
                    break
                if name.casefold() == destination_name:
                    continue
                jobs, seen, before = [], set(), None
                for page in range(15):  # At most 300 posts / 15 pages per source.
                    if not self._fill_allowed(run) or name not in self.web_sources:
                        break
                    try:
                        posts = await webfeed.fetch(self.http, name, before=before)
                    except Exception:
                        run.source_errors += 1
                        break
                    fresh = [p for p in posts if p.id not in seen and (before is None or p.id < before)]
                    if not fresh:
                        break  # Exhausted, repeated page, or ignored pagination cursor.
                    fresh.sort(key=lambda p: p.id, reverse=True)
                    fresh = fresh[:300 - len(seen)]
                    for post in fresh:
                        seen.add(post.id)
                        if self.cfg.require_photo and not post.photo_url:
                            continue
                        offer = parse(post.text, post.urls)
                        if offer:
                            jobs.append(Job(0, post.id, offer, post.photo_url, True, f"https://t.me/{name}/{post.id}"))
                    if len(seen) >= 300:
                        break
                    before = min(p.id for p in fresh)
                    if page < 14 and not await self._publication_wait(1, run):
                        break
                if jobs:
                    candidates[name] = jobs
        else:
            sources = [s for s in self.store.list_sources() if s.active and s.chat_id != run.destination]
            random.shuffle(sources)
            for source in sources:
                if not self._fill_allowed(run):
                    break
                try:
                    messages = await self.client.get_messages(source.chat_id, limit=300)
                except Exception:
                    run.source_errors += 1
                    continue
                jobs = []
                for message in messages:
                    if source.topic_id and topic_of(message) != source.topic_id:
                        continue
                    if self.cfg.require_photo and not message.photo:
                        continue
                    offer = parse(message.raw_text or "", collect_urls(message))
                    if offer:
                        jobs.append(Job(source.chat_id, message.id, offer))
                if jobs:
                    candidates[str(source.chat_id)] = jobs
        return candidates

    async def _run_fill(self, run: FillRun):
        try:
            candidates = await self._fill_candidates(run)
            if self._fill_allowed(run):
                run.phase = "a publicar (respeitando os limites)"
                await self.notify(
                    f"📦 Recolhi {sum(map(len, candidates.values()))} ofertas em {len(candidates)} fontes. "
                    f"Vou tentar publicar {run.target}, sem repetir produtos neste lote."
                )
            for job in interleave(candidates):
                if not self._fill_allowed(run) or run.published >= run.target:
                    break
                run.attempted += 1
                try:
                    reason = await self.process(job, fill=run)
                except Exception:
                    reason = "Falha a preparar a oferta."
                    log.warning("Falhou uma candidata do enchimento")
                if reason:
                    run.skipped += 1
                elif run.published % 10 == 0:
                    await self.notify(f"📦 Enchimento: {run.published}/{run.target} publicadas.")
                await asyncio.sleep(0)  # Allow stop/status commands between fast operations.
            if run.published >= run.target:
                run.phase = "concluído"
            elif not self._fill_allowed(run):
                run.phase = "interrompido"
            else:
                run.phase = "terminado: esgotaram-se as ofertas válidas recolhidas"
        except asyncio.CancelledError:
            run.phase = "interrompido pelo encerramento do bot"
            run.stop.set()
            raise
        except Exception:
            run.phase = "terminado com erro na leitura ou preparação"
            run.stop.set()
            log.warning("Falhou o enchimento do canal")
        await self.notify(
            f"📦 Enchimento {run.phase}: <b>{run.published}/{run.target}</b> publicadas. "
            f"Não publicadas: {run.skipped}; falhas na leitura de fontes: {run.source_errors}. "
            "Podes iniciar outro lote com /encher."
        )

    async def force_recent(self, n: int = 5, nolink: bool = False) -> None:
        """Mostra as n ofertas mais recentes da origem, ignorando 'só novas' e o
        anti-duplicados. Cada uma chega com Publicar/Rejeitar; nada é publicado sozinho."""
        n = max(1, min(int(n), 10))

        async def say(text: str, **kw) -> None:
            await self.bot.send_message(self.owner, text, parse_mode="html", link_preview=False, **kw)

        jobs: list[Job] = []
        skipped = 0
        errs: list[str] = []
        if self.web:
            names = self.web_sources
            if not names:
                await say("Sem fontes. Usa /adicionar t.me/nomedocanal")
                return
            for name in names:
                try:
                    posts = await webfeed.fetch(self.http, name)
                except Exception as e:
                    errs.append(f"{html.escape(name)}: {html.escape(str(e))}")
                    continue
                picked, sk = webfeed.recent_offers(posts, n, self.cfg.require_photo)
                skipped += sk
                for p, offer in reversed(picked):  # as mais antigas primeiro; a mais nova chega por último
                    jobs.append(Job(0, p.id, offer, photo_url=p.photo_url, web=True, origin=f"https://t.me/{name}/{p.id}"))
        else:
            srcs = [s for s in self.store.list_sources() if s.active]
            if not srcs:
                await say("Sem fontes. Usa /escolher ou /adicionar LINK.")
                return
            for s in srcs:
                try:
                    msgs = await self.client.get_messages(s.chat_id, limit=80)  # da mais nova para a mais antiga
                except Exception as e:
                    errs.append(f"{html.escape(s.title or str(s.chat_id))}: {html.escape(str(e))}")
                    continue
                found: list[Job] = []
                for m in msgs:
                    if len(found) >= n:
                        break
                    if s.topic_id and topic_of(m) != s.topic_id:
                        continue
                    if self.cfg.require_photo and not m.photo:
                        skipped += 1
                        continue
                    offer = parse(m.raw_text or "", collect_urls(m))
                    if not offer:
                        skipped += 1
                        continue
                    found.append(Job(s.chat_id, m.id, offer))
                jobs.extend(reversed(found))

        if not jobs:
            extra = ("\n" + "\n".join(errs)) if errs else ""
            await say("Não encontrei ofertas recentes (com imagem e link) na origem." + extra)
            return

        if nolink:
            await say(
                f"🧪 A preparar <b>{len(jobs)}</b> oferta(s) <b>sem link</b> (teste). "
                "Se carregares em <b>Publicar</b>, vai para o canal de destino tal como está."
            )
        else:
            await say(f"🔄 A preparar <b>{len(jobs)}</b> oferta(s). Carrega em <b>Publicar</b> nas que quiseres.")
        fails: list[str] = []
        for i, job in enumerate(jobs, 1):
            try:
                err = await self.process(job, force=True, nolink=nolink)
            except Exception as e:
                log.exception("Erro no modo forçado")
                err = f"⚠️ Erro: {html.escape(str(e))}"
            if err:
                fails.append(f"<b>{i}.</b> {err}")
        ok = len(jobs) - len(fails)
        resumo = [f"✅ {ok} enviada(s) para aprovação" + (" (sem link, só teste)." if nolink else ".")]
        if skipped:
            resumo.append(f"{skipped} mensagem(ns) ignorada(s) (sem imagem ou sem link).")
        resumo += errs
        if fails:
            resumo.append("\n<b>Não consegui converter:</b>\n" + "\n\n".join(fails))
        await say("\n".join(resumo), buttons=self.main_buttons())

    async def cmd_pausar(self, event, arg):
        self.store.set("paused", True)
        if self.fill_run:
            self.fill_run.stop.set()
        await event.respond("⏸ Em pausa. Não leio nem publico até /retomar.")

    async def cmd_retomar(self, event, arg):
        self.store.set("paused", False)
        await event.respond("▶️ Retomado.")

    async def cmd_auto(self, event, arg):
        self.store.set("mode", "auto")
        await event.respond("⚡ Modo automático: publico sem pedir aprovação.")

    async def cmd_manual(self, event, arg):
        self.store.set("mode", "manual")
        await event.respond("✋ Modo manual: cada oferta chega aqui para aprovares.")

    async def cmd_codigo(self, event, arg):
        if not arg:
            await event.respond(f"Código atual: <code>{html.escape(self.code or '(vazio)')}</code>\nUso: /codigo NOVO14", parse_mode="html")
            return
        self.store.set("code", arg)
        await event.respond(f"✅ Código mudado para <code>{html.escape(arg)}</code>", parse_mode="html")

    async def cmd_limite(self, event, arg):
        if not arg.isdigit() or not (1 <= int(arg) <= 200):
            await event.respond("Uso: /limite 12 (entre 1 e 200 por hora)")
            return
        self.store.set("max_per_hour", int(arg))
        await event.respond(f"✅ Limite: {arg} publicações por hora.")

    async def cmd_modelo(self, event, arg):
        self.awaiting = "modelo"
        nota = (
            "\nℹ️ No modo sem conta os emojis premium não são enviados (saem como emoji normal)."
            if self.web else ""
        )
        await event.respond(
            "Modelo atual:\n\n"
            f"<pre>{html.escape(render.to_plain(self.template))}</pre>\n"
            "Para mudar, envia-me <b>a próxima mensagem</b> já com o texto e os emojis que queres. "
            "Usa estes campos:\n"
            "<code>{titulo}</code> <code>{preco}</code> <code>{codigo}</code> <code>{link}</code>\n"
            "{link} é obrigatório. Se faltar um campo numa oferta (ex.: sem preço), essa linha é omitida.\n"
            "Qualquer comando cancela." + nota,
            parse_mode="html",
        )

    async def set_template(self, event) -> None:
        self.awaiting = None
        tpl = render.from_telegram(event.raw_text, event.message.entities)
        if not render.has_link_field(tpl):
            await event.respond("⚠️ O modelo tem de ter {link}. Não guardei. Tenta outra vez com /modelo.")
            return
        self.store.set("template", tpl)
        n_ce = tpl.count("{ce:")
        extra = ""
        if n_ce and (self.web or not self.premium):
            extra = f"\nℹ️ {n_ce} emoji(s) premium guardados, mas aqui saem como emoji normal."
        preview, _ = render.render(
            tpl, {"titulo": "Adidas Spezial", "preco": "25€", "codigo": self.code or "CODIGO", "link": "https://exemplo"}, premium=False
        )
        await event.respond(f"✅ Modelo guardado.{extra}\n\nExemplo:\n\n{preview}", link_preview=False)

    async def cmd_linkmodelo(self, event, arg):
        if not arg:
            await event.respond(
                "Cola um link de <b>um produto</b> gerado na tua conta de afiliado:\n"
                "<code>/linkmodelo https://www.hacoo.pl/detail/123?f=...</code>\n"
                "Eu troco só o número do produto, mantendo o resto (o teu identificador).",
                parse_mode="html",
            )
            return
        sample = find_urls(arg)
        sample = sample[0] if sample else arg
        rules = links.load_rules(self.cfg.rules_path)
        try:
            sample = await links.resolve(sample, rules, self.http)
        except Exception:
            pass
        res = links.template_from_sample(rules, sample)
        if not res:
            await event.respond("⚠️ Não reconheço esse link (loja não suportada ou sem ID de produto).")
            return
        rule, template = res
        rule.template = template
        links.save_rules(self.cfg.rules_path, rules)
        self.warned_template = False
        await event.respond(
            f"✅ Link modelo de <b>{rule.name}</b> guardado:\n<code>{html.escape(template)}</code>\n"
            "Confirma que o teu identificador de afiliado aparece aí.",
            parse_mode="html",
        )

    async def cmd_recarregar(self, event, arg):
        rules = links.load_rules(self.cfg.rules_path)
        await event.respond(f"✅ {len(rules)} regra(s) carregada(s).")

    async def cmd_ultimas(self, event, arg):
        rows = self.store.recent(10)
        if not rows:
            await event.respond("Ainda não publiquei nada.")
            return
        out = []
        for r in rows:
            when = datetime.fromtimestamp(r["ts"]).strftime("%d/%m %H:%M")
            out.append(f"{when} · {r['title'] or r['key']}\n{r['url']}")
        await event.respond("\n\n".join(out), link_preview=False)

    # ---------- fontes ----------
    async def cmd_fontes(self, event, arg):
        if self.web:
            names = self.web_sources
            if not names:
                await event.respond("Sem fontes. Usa /adicionar t.me/nomedocanal")
                return
            await event.respond("<b>Canais públicos</b>\n" + "\n".join(f"• t.me/{html.escape(n)}" for n in names), parse_mode="html")
            return
        srcs = self.store.list_sources()
        if not srcs:
            await event.respond("Sem fontes. Usa /escolher ou /adicionar LINK.")
            return
        lines = ["<b>Fontes</b>"]
        for s in srcs:
            topico = f" · tópico {s.topic_id}" if s.topic_id else ""
            lines.append(f"• {html.escape(s.title or str(s.chat_id))} (<code>{s.chat_id}</code>){topico}")
        await event.respond("\n".join(lines), parse_mode="html")

    async def cmd_remover(self, event, arg):
        if self.web:
            names = self.web_sources
            if not names:
                await event.respond("Sem fontes.")
                return
            rows = [[Button.inline(f"🗑 {n}"[:40], f"w:{n}")] for n in names]
            await event.respond("Qual canal queres remover?", buttons=rows)
            return
        srcs = self.store.list_sources()
        if not srcs:
            await event.respond("Sem fontes.")
            return
        rows = [[Button.inline(f"🗑 {(s.title or str(s.chat_id))[:36]}", f"r:{s.chat_id}")] for s in srcs]
        await event.respond("Qual fonte queres remover?", buttons=rows)

    async def cmd_adicionar(self, event, arg):
        if self.web:
            await self._add_web_source(event, arg)
            return
        if not arg:
            await event.respond("Uso: /adicionar https://t.me/+convite  (ou t.me/nome, ou @nome)")
            return
        try:
            ent = await self._join(arg)
        except Exception as e:
            await event.respond(f"⚠️ Não consegui entrar: {e}")
            return
        cid = utils.get_peer_id(ent)
        title = getattr(ent, "title", None) or str(cid)
        self.store.add_source(cid, title)
        await event.respond(f"✅ Fonte adicionada: {html.escape(title)} (<code>{cid}</code>)", parse_mode="html")

    async def _add_web_source(self, event, arg):
        name = webfeed.normalize_channel(arg)
        if not name:
            await event.respond(
                "Uso: /adicionar t.me/nomedocanal\nSó funciona com canais <b>públicos</b> (links de convite privados não).",
                parse_mode="html",
            )
            return
        try:
            posts = await webfeed.fetch(self.http, name)
        except Exception as e:
            await event.respond(f"⚠️ Não consegui ler t.me/s/{name}: {e}")
            return
        names = self.web_sources
        if name not in names:
            names.append(name)
            self.store.set("web_sources", names)
        self.store.set(f"web_last:{name}", max(p.id for p in posts))
        await event.respond(
            f"✅ Canal t.me/{html.escape(name)} adicionado ({len(posts)} mensagens visíveis).\n"
            "Só publico as ofertas <b>novas</b>, a partir de agora.",
            parse_mode="html",
        )

    async def cmd_escolher(self, event, arg):
        if self.web:
            await event.respond("No modo sem conta usa /adicionar t.me/nomedocanal (canais públicos).")
            return
        rows, _ = await self._dialog_buttons("src")
        if not rows:
            await event.respond("Não encontrei grupos. Entra no grupo com a tua conta e tenta de novo, ou usa /adicionar LINK.")
            return
        await event.respond("Toca para ligar/desligar grupos de origem (✅ = ativo):", buttons=rows)

    async def cmd_topico(self, event, arg):
        if self.web:
            await event.respond("Os tópicos só existem no modo com conta.")
            return
        parts = arg.split()
        if len(parts) != 2 or not all(re.fullmatch(r"-?\d+", p) for p in parts):
            await event.respond("Uso: /topico ID_FONTE ID_TOPICO (0 = todos). O ID da fonte vê-se em /fontes.")
            return
        chat_id, topic = int(parts[0]), int(parts[1])
        ok = self.store.set_topic(chat_id, topic or None)
        await event.respond("✅ Guardado." if ok else "⚠️ Fonte não encontrada.")

    # ---------- destino ----------
    async def cmd_destino(self, event, arg):
        if not self.web:
            rows, _ = await self._dialog_buttons("dest")
            if not rows:
                await event.respond("Não encontrei canais onde possas publicar. A tua conta tem de ser administradora do canal.")
                return
            await event.respond("Escolhe o canal onde publico:", buttons=rows)
            return
        if arg:
            name = webfeed.normalize_channel(arg)
            if not name:
                await event.respond("Uso: /destino @nomedocanal (canal público). Para um canal privado, envia /destino e reencaminha-me uma mensagem do canal.")
                return
            try:
                ent = await self.bot.get_entity(name)
            except Exception as e:
                await event.respond(f"⚠️ Não encontrei o canal: {e}")
                return
            await self._save_dest(event, ent)
            return
        self.awaiting = "destino"
        await event.respond(
            "Primeiro adiciona <b>este bot como administrador</b> do teu canal (com permissão para publicar mensagens).\n"
            "Depois envia-me o <code>@nomedocanal</code>, ou <b>reencaminha-me uma mensagem</b> do canal (serve para canais privados).",
            parse_mode="html",
        )

    async def set_dest_from_message(self, event) -> None:
        self.awaiting = None
        fwd = event.message.forward
        ent = None
        if fwd:
            ent = await fwd.get_chat()
        else:
            name = webfeed.normalize_channel(event.raw_text or "")
            if name:
                ent = await self.bot.get_entity(name)
        if ent is None:
            await event.respond("⚠️ Não percebi. Envia /destino e tenta outra vez com @nomedocanal ou uma mensagem reencaminhada.")
            return
        await self._save_dest(event, ent)

    async def _save_dest(self, event, ent) -> None:
        cid = utils.get_peer_id(ent)
        title = getattr(ent, "title", None) or str(cid)
        aviso = ""
        try:
            me = await self.bot.get_me()
            perms = await self.bot.get_permissions(ent, me)
            if not perms.is_admin or not getattr(perms, "post_messages", True):
                aviso = "\n⚠️ O bot ainda <b>não é administrador</b> com permissão para publicar. Adiciona-o como admin antes de ligares o /auto."
        except Exception:
            aviso = "\n⚠️ Não consegui confirmar que o bot é administrador do canal. Garante que é, com permissão para publicar."
        self.store.set("dest", cid)
        self.store.set("dest_title", title)
        await event.respond(f"✅ Vou publicar em: <b>{html.escape(title)}</b>{aviso}", parse_mode="html")

    # ---------- só modo conta ----------
    async def _dialog_buttons(self, kind: str):
        rows, titles = [], {}
        count = 0
        active = {s.chat_id for s in self.store.list_sources()}
        async for d in self.client.iter_dialogs(limit=300):
            if kind == "src" and not (d.is_group or d.is_channel):
                continue
            if kind == "dest":
                ent = d.entity
                ok = d.is_channel and getattr(ent, "broadcast", False) and (
                    getattr(ent, "creator", False)
                    or getattr(getattr(ent, "admin_rights", None), "post_messages", False)
                )
                if not ok:
                    continue
            mark = "✅ " if (kind == "src" and d.id in active) else ""
            rows.append([Button.inline((mark + (d.name or str(d.id)))[:40], f"{'s' if kind == 'src' else 'd'}:{d.id}")])
            titles[d.id] = d.name
            count += 1
            if count >= 40:
                break
        return rows, titles

    async def _join(self, link: str):
        link = link.strip()
        m = re.search(r"t\.me/(?:\+|joinchat/)([\w-]+)", link)
        if m:
            h = m.group(1)
            try:
                upd = await self.client(ImportChatInviteRequest(h))
                return upd.chats[0]
            except errors.UserAlreadyParticipantError:
                info = await self.client(CheckChatInviteRequest(h))
                return info.chat
        name = re.sub(r"^(?:https?://)?(?:t\.me/|@)", "", link).strip("/")
        ent = await self.client.get_entity(name)
        try:
            await self.client(JoinChannelRequest(ent))
        except Exception:
            pass
        return ent

    # ---------- botões ----------
    async def on_button(self, event) -> None:
        if event.sender_id != self.owner or event.chat_id != self.owner:
            await event.answer("Sem permissão.", alert=True)
            return
        data = event.data.decode()
        kind, _, val = data.partition(":")
        try:
            if kind in ("ok", "no"):
                payload = self.store.pop_pending(int(val))
                if not payload:
                    await event.answer("Já tratada.")
                    return
                if kind == "no":
                    await event.answer("Rejeitada.")
                    await self._mark(event, "❌ Rejeitada")
                    return
                await event.answer("A publicar…")
                ok = await self.publish(payload)
                await self._mark(event, "✅ Publicada" if ok else "⚠️ Falhou a publicação")
            elif kind == "f":
                await event.answer("A obter as últimas ofertas…")
                await self.force_recent(int(val) if val.isdigit() else 5)
            elif kind == "g":
                await event.answer("A obter as últimas ofertas (sem link)…")
                await self.force_recent(int(val) if val.isdigit() else 5, nolink=True)
            elif kind == "w":
                names = [n for n in self.web_sources if n != val]
                self.store.set("web_sources", names)
                await event.answer("Removido.")
                await event.edit(f"🗑 Canal {val} removido.")
            elif kind == "s" and self.client:
                cid = int(val)
                if self.store.get_source(cid):
                    self.store.remove_source(cid)
                    await event.answer("Fonte removida.")
                else:
                    title = ""
                    async for d in self.client.iter_dialogs(limit=300):
                        if d.id == cid:
                            title = d.name
                            break
                    self.store.add_source(cid, title)
                    await event.answer("Fonte ligada.")
                rows, _ = await self._dialog_buttons("src")
                await event.edit("Toca para ligar/desligar grupos de origem (✅ = ativo):", buttons=rows)
            elif kind == "d" and self.client:
                cid = int(val)
                title = ""
                async for d in self.client.iter_dialogs(limit=300):
                    if d.id == cid:
                        title = d.name
                        break
                self.store.set("dest", cid)
                self.store.set("dest_title", title)
                await event.answer("Destino guardado.")
                await event.edit(f"✅ Vou publicar em: {title}")
            elif kind == "r":
                self.store.remove_source(int(val))
                await event.answer("Removida.")
                await event.edit("🗑 Fonte removida.")
        except Exception as e:
            log.exception("Erro num botão")
            await event.answer(f"Erro: {e}"[:190], alert=True)

    async def _mark(self, event, label: str) -> None:
        try:
            msg = await event.get_message()
            cap = (msg.raw_text or "")
            await event.edit(f"{label}\n\n{cap}"[:1000], buttons=None)
        except Exception:
            pass


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = Config.load()
    app = App(cfg)
    asyncio.run(app.start())


if __name__ == "__main__":
    main()
