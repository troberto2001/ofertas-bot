"""Bot de ofertas: lê grupos, converte o link para o teu e publica no teu canal.

Duas ligações ao Telegram na mesma aplicação:
  - a TUA conta (userbot): lê os grupos de origem e publica no canal
    (é a única forma de ler grupos onde não podes adicionar bots e de enviar
    emojis premium);
  - um bot normal (BotFather): é o teu painel de controlo, só responde a ti.
"""
from __future__ import annotations

import asyncio
import html
import io
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime

from telethon import Button, TelegramClient, errors, events, utils
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import MessageEntityCustomEmoji

from . import links, render
from .config import Config
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
/modelo – ver/mudar o modelo da mensagem (aceita emojis premium)
/linkmodelo LINK – cola um link teu da loja (gerado na tua conta)
/limite N – máximo de publicações por hora

<b>Grupos</b>
/escolher – escolher grupos de origem (botões)
/adicionar LINK – entrar num grupo por link e usá-lo como fonte
/fontes – ver fontes ativas · /remover – tirar uma fonte
/topico ID_FONTE ID_TOPICO – só copiar de um tópico (0 = todos)
/destino – escolher o canal onde publicar
/recarregar – recarregar o ficheiro rules.json"""


@dataclass
class Job:
    chat_id: int
    msg_id: int
    offer: Offer


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
        self.store = Store(cfg.db_path)
        self.client = TelegramClient(cfg.user_session, cfg.api_id, cfg.api_hash)
        self.bot = TelegramClient(cfg.bot_session, cfg.api_id, cfg.api_hash)
        self.http = links.make_client()
        self.queue: asyncio.Queue[Job] = asyncio.Queue()
        self.owner: int = cfg.owner_id or 0
        self.premium = False
        self.awaiting: str | None = None
        self.idle_alerted = False
        self.warned_template = False
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

    # ---------- avisos ----------
    async def notify(self, text: str, **kw) -> None:
        try:
            await self.bot.send_message(self.owner, text, parse_mode="html", **kw)
        except Exception as e:  # o dono ainda não abriu o bot, etc.
            log.warning("Aviso via bot falhou (%s); a enviar para as Mensagens Guardadas", e)
            try:
                await self.client.send_message("me", re.sub(r"<[^>]+>", "", text))
            except Exception:
                log.exception("Não consegui avisar o dono")

    # ---------- arranque ----------
    async def start(self) -> None:
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise SystemExit("Conta não autenticada. Corre: botctl login")
        me = await self.client.get_me()
        self.premium = bool(getattr(me, "premium", False))
        if not self.owner:
            self.owner = me.id
        await self.bot.start(bot_token=self.cfg.bot_token)
        links.load_rules(self.cfg.rules_path)  # cria o ficheiro por omissão

        self.client.add_event_handler(self.on_source_message, events.NewMessage())
        self.bot.add_event_handler(
            self.on_command, events.NewMessage(from_users=self.owner, incoming=True)
        )
        self.bot.add_event_handler(self.on_button, events.CallbackQuery())

        log.info("Pronto. Dono=%s premium=%s modo=%s", self.owner, self.premium, self.mode)
        await self.notify(
            "🤖 Bot iniciado.\n"
            + ("✅ Telegram Premium ativo." if self.premium else "ℹ️ Sem Premium: emojis premium viram emojis normais.")
            + "\nEnvia /estado ou /ajuda."
        )
        await asyncio.gather(
            self.worker(),
            self.idle_monitor(),
            self.client.run_until_disconnected(),
            self.bot.run_until_disconnected(),
        )

    # ---------- leitura dos grupos ----------
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

    async def worker(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                await self.process(job)
            except Exception as e:
                log.exception("Erro a processar oferta")
                await self.notify(f"⚠️ Erro a processar uma oferta: {e}")
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
        origem = msg_link(job.chat_id, job.msg_id)
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
        link = links.build_link(rule, pid)
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
        payload = {"chat_id": job.chat_id, "msg_id": job.msg_id, "values": values, "key": key}
        if self.mode == "auto":
            await self.publish(payload)
        else:
            await self.send_for_approval(payload)

    # ---------- aprovação manual ----------
    async def send_for_approval(self, payload: dict) -> None:
        pid = self.store.add_pending(payload["key"], payload)
        text, _ = render.render(self.template, payload["values"], premium=False)
        text = render.to_plain(text)
        buttons = [[Button.inline("✅ Publicar", f"ok:{pid}"), Button.inline("❌ Rejeitar", f"no:{pid}")]]
        try:
            m = await self.client.get_messages(payload["chat_id"], ids=payload["msg_id"])
            if m and m.photo:
                data = await self.client.download_media(m, file=bytes)
                f = io.BytesIO(data)
                f.name = "oferta.jpg"
                await self.bot.send_file(self.owner, f, caption=text[:1000], buttons=buttons, link_preview=False)
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
            await self.notify("⚠️ Falta escolher o canal de destino: /destino")
            return False
        m = await self.client.get_messages(payload["chat_id"], ids=payload["msg_id"])
        if not m:
            await self.notify("⚠️ A mensagem original já não existe. Não publiquei.")
            return False
        await self._throttle()
        text, spec = render.render(self.template, payload["values"], premium=self.premium)
        ents = [MessageEntityCustomEmoji(o, l, d) for o, l, d in spec] or None
        for attempt in (1, 2):
            try:
                if m.photo:
                    await self.client.send_file(
                        dest, m.photo, caption=text, formatting_entities=ents, parse_mode=None
                    )
                else:
                    await self.client.send_message(
                        dest, text, formatting_entities=ents, parse_mode=None, link_preview=False
                    )
                break
            except errors.FloodWaitError as e:
                if attempt == 2:
                    await self.notify(f"⚠️ Telegram pediu para esperar {e.seconds}s. Não publiquei.")
                    return False
                await asyncio.sleep(e.seconds + 1)
            except Exception as e:
                log.exception("Falhou a publicar")
                await self.notify(f"⚠️ Falhou a publicação: {e}")
                return False
        self.store.mark_posted(
            payload["key"], payload["chat_id"], payload["msg_id"],
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
                if self.paused or self.idle_alerted or not self.store.list_sources():
                    continue
                last = self.store.last_post_ts() or self.started
                if time.time() - last > self.cfg.idle_alert_hours * 3600:
                    self.idle_alerted = True
                    await self.notify(
                        f"⏰ Há mais de {self.cfg.idle_alert_hours}h sem publicar nada. "
                        "Pode ser normal, ou o grupo mudou. Vê /estado."
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
        except Exception as e:
            log.exception("Erro num comando")
            await event.respond(f"⚠️ Erro: {e}")

    async def cmd_start(self, event, arg):
        await event.respond(AJUDA, parse_mode="html")

    cmd_ajuda = cmd_start

    async def cmd_estado(self, event, arg):
        srcs = self.store.list_sources()
        dest = self.s("dest_title") or ("não definido" if not self.s("dest") else str(self.s("dest")))
        rules = links.load_rules(self.cfg.rules_path)
        sem_modelo = [r.name for r in rules if not r.template]
        last = self.store.last_post_ts()
        ultima = (
            datetime.fromtimestamp(last).strftime("%d/%m %H:%M") if last else "—"
        )
        lines = [
            "<b>Estado</b>",
            f"{'⏸ Em pausa' if self.paused else '▶️ A correr'} · modo <b>{'automático' if self.mode == 'auto' else 'manual (aprovas tu)'}</b>",
            f"Fontes ativas: {len(srcs)} · Destino: {dest}",
            f"Código: <code>{self.code or '(vazio)'}</code>",
            f"Premium: {'sim' if self.premium else 'não'}",
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
            await event.respond(f"Código atual: <code>{self.code or '(vazio)'}</code>\nUso: /codigo NOVO14", parse_mode="html")
            return
        self.store.set("code", arg)
        await event.respond(f"✅ Código mudado para <code>{arg}</code>", parse_mode="html")

    async def cmd_limite(self, event, arg):
        if not arg.isdigit() or not (1 <= int(arg) <= 200):
            await event.respond("Uso: /limite 12 (entre 1 e 200 por hora)")
            return
        self.store.set("max_per_hour", int(arg))
        await event.respond(f"✅ Limite: {arg} publicações por hora.")

    async def cmd_modelo(self, event, arg):
        self.awaiting = "modelo"
        await event.respond(
            "Modelo atual:\n\n"
            f"<pre>{html.escape(render.to_plain(self.template))}</pre>\n"
            "Para mudar, envia-me <b>a próxima mensagem</b> já com o texto e os emojis que queres "
            "(podes usar emojis premium). Usa estes campos:\n"
            "<code>{titulo}</code> <code>{preco}</code> <code>{codigo}</code> <code>{link}</code>\n"
            "{link} é obrigatório. Se faltar um campo numa oferta (ex.: sem preço), essa linha é omitida.\n"
            "Qualquer comando cancela.",
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
        if n_ce and not self.premium:
            extra = f"\nℹ️ {n_ce} emoji(s) premium guardados, mas esta conta não tem Premium: saem como emoji normal."
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
        try:
            sample = await links.resolve(sample, links.load_rules(self.cfg.rules_path), self.http)
        except Exception:
            pass
        rules = links.load_rules(self.cfg.rules_path)
        res = links.template_from_sample(rules, sample)
        if not res:
            await event.respond("⚠️ Não reconheço esse link (loja não suportada ou sem ID de produto).")
            return
        rule, template = res
        rule.template = template
        links.save_rules(self.cfg.rules_path, rules)
        self.warned_template = False
        await event.respond(
            f"✅ Link modelo de <b>{rule.name}</b> guardado:\n<code>{template}</code>\n"
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

    # ---------- fontes e destino ----------
    async def cmd_fontes(self, event, arg):
        srcs = self.store.list_sources()
        if not srcs:
            await event.respond("Sem fontes. Usa /escolher ou /adicionar LINK.")
            return
        lines = ["<b>Fontes</b>"]
        for s in srcs:
            topico = f" · tópico {s.topic_id}" if s.topic_id else ""
            lines.append(f"• {s.title or s.chat_id} (<code>{s.chat_id}</code>){topico}")
        await event.respond("\n".join(lines), parse_mode="html")

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

    async def cmd_escolher(self, event, arg):
        rows, _ = await self._dialog_buttons("src")
        if not rows:
            await event.respond("Não encontrei grupos. Entra no grupo com a tua conta e tenta de novo, ou usa /adicionar LINK.")
            return
        await event.respond("Toca para ligar/desligar grupos de origem (✅ = ativo):", buttons=rows)

    async def cmd_destino(self, event, arg):
        rows, _ = await self._dialog_buttons("dest")
        if not rows:
            await event.respond("Não encontrei canais onde possas publicar. A tua conta tem de ser administradora do canal.")
            return
        await event.respond("Escolhe o canal onde publico:", buttons=rows)

    async def cmd_remover(self, event, arg):
        srcs = self.store.list_sources()
        if not srcs:
            await event.respond("Sem fontes.")
            return
        rows = [[Button.inline(f"🗑 {(s.title or str(s.chat_id))[:36]}", f"r:{s.chat_id}")] for s in srcs]
        await event.respond("Qual fonte queres remover?", buttons=rows)

    async def cmd_topico(self, event, arg):
        parts = arg.split()
        if len(parts) != 2 or not all(re.fullmatch(r"-?\d+", p) for p in parts):
            await event.respond("Uso: /topico ID_FONTE ID_TOPICO (0 = todos). O ID da fonte vê-se em /fontes.")
            return
        chat_id, topic = int(parts[0]), int(parts[1])
        ok = self.store.set_topic(chat_id, topic or None)
        await event.respond("✅ Guardado." if ok else "⚠️ Fonte não encontrada.")

    async def cmd_adicionar(self, event, arg):
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
        await event.respond(f"✅ Fonte adicionada: {title} (<code>{cid}</code>)", parse_mode="html")

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
            elif kind == "s":
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
            elif kind == "d":
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
    cfg = Config.load()
    app = App(cfg)
    asyncio.run(app.start())


if __name__ == "__main__":
    main()
