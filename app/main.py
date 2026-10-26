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
from dataclasses import dataclass
from datetime import datetime

from telethon import Button, TelegramClient, errors, events, utils
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import MessageEntityCustomEmoji

from . import links, render, webfeed
from .config import Config
from .links import UA
from .parser import Offer, find_urls, parse
from .store import Store

log = logging.getLogger("ofertas")

AJUDA = """<b>Comandos</b>

<b>Estado</b>
/estado – resumo do bot
/ultimas – últimas publicações
/pausar · /retomar – parar/continuar

<b>Publicação</b>
/manual – cada oferta chega aqui para aprovares
/auto – publica sozinho
/codigo CODIGO – muda o teu código de desconto
/modelo – ver/mudar o modelo da mensagem
/linkmodelo LINK – cola um link teu da loja (gerado na tua conta)
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
        await asyncio.gather(*tasks)

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

    async def process(self, job: Job) -> None:
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
            await self.notify(
                "⚠️ Loja não suportada ou link que não consegui seguir. <b>Não publiquei.</b>\n"
                f"Links: {' '.join(job.offer.urls)}\nOrigem: {origem}"
            )
            return
        pid = links.extract_id(rule, final)
        if not pid:
            await self.notify(
                f"⚠️ O formato do link da loja <b>{rule.name}</b> mudou (não encontro o ID do produto). "
                f"<b>Não publiquei.</b>\n{final}\nOrigem: {origem}"
            )
            return
        link = links.build_link(rule, pid, final)
        if not link:
            if not self.warned_template:
                self.warned_template = True
                await self.notify(
                    "⚠️ Ainda não tenho o teu link de afiliado da loja "
                    f"<b>{rule.name}</b>. Gera um link de um produto na tua conta e envia:\n"
                    "<code>/linkmodelo SEU_LINK</code>\nAté lá não publico nada."
                )
            return

        key = f"{rule.name}:{pid}"
        if self.store.was_posted(key, self.cfg.dedup_hours) or self.store.has_pending_key(key):
            log.info("Duplicado ignorado: %s", key)
            return

        values = {
            "titulo": job.offer.title,
            "preco": job.offer.price,
            "codigo": self.code or None,
            "link": link,
        }
        payload = {
            "chat_id": job.chat_id, "msg_id": job.msg_id, "values": values, "key": key,
            "web": job.web, "photo_url": job.photo_url, "origin": origem,
        }
        if self.mode == "auto":
            await self.publish(payload)
        else:
            await self.send_for_approval(payload)

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
    async def send_for_approval(self, payload: dict) -> None:
        pid = self.store.add_pending(payload["key"], payload)
        text, _ = render.render(self.template, payload["values"], premium=False)
        text = render.to_plain(text)
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
    async def _throttle(self) -> None:
        while self.store.count_since(3600) >= self.max_per_hour:
            await asyncio.sleep(30)
        gap = time.time() - (self.store.last_post_ts() or 0)
        if gap < self.cfg.min_interval:
            await asyncio.sleep(self.cfg.min_interval - gap)

    async def publish(self, payload: dict) -> bool:
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
        await self._throttle()
        premium = False if web else self.premium
        text, spec = render.render(self.template, payload["values"], premium=premium)
        ents = [MessageEntityCustomEmoji(o, l, d) for o, l, d in spec] or None
        sender = self.bot if web else self.client
        for attempt in (1, 2):
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
                await asyncio.sleep(e.seconds + 1)
            except Exception as e:
                log.exception("Falhou a publicar")
                await self.notify(
                    f"⚠️ Falhou a publicação: {html.escape(str(e))}\n"
                    "Confirma que o bot é administrador do canal de destino com permissão para publicar."
                )
                return False
        self.store.mark_posted(
            payload["key"], payload.get("chat_id") or 0, payload.get("msg_id") or 0,
            payload["values"].get("titulo") or "", payload["values"]["link"],
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

    async def cmd_start(self, event, arg):
        await event.respond(AJUDA, parse_mode="html")

    cmd_ajuda = cmd_start

    async def cmd_estado(self, event, arg):
        dest = self.s("dest_title") or ("não definido" if not self.s("dest") else str(self.s("dest")))
        rules = links.load_rules(self.cfg.rules_path)
        sem_modelo = [r.name for r in rules if not r.template]
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
        ]
        if sem_modelo:
            lines.append(f"⚠️ Falta o link modelo de: {', '.join(sem_modelo)} (/linkmodelo)")
        await event.respond("\n".join(lines), parse_mode="html")

    async def cmd_pausar(self, event, arg):
        self.store.set("paused", True)
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
        if event.sender_id != self.owner:
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
