"""Login interativo da conta de Telegram (só no modo 'account').

    docker compose run --rm -it bot python -m app.login

No pedido "phone (or bot token)" escreve o NÚMERO DE TELEMÓVEL (+351...).
Nunca o token do BotFather: um bot não consegue ler grupos.
"""
import asyncio
import os
import sys

from telethon import TelegramClient

from .config import Config


async def main() -> None:
    cfg = Config.load(need_bot=False)
    client = TelegramClient(cfg.user_session, cfg.api_id, cfg.api_hash)
    print("Escreve o teu NÚMERO DE TELEMÓVEL com indicativo (ex.: +351912345678).")
    print("NÃO colar o token do BotFather.\n")
    await client.start()
    me = await client.get_me()
    if getattr(me, "bot", False):
        await client.disconnect()
        for ext in ("", "-journal"):
            try:
                os.remove(cfg.user_session + ".session" + ext)
            except FileNotFoundError:
                pass
        print("\nERRO: entraste com o token de um bot. Sessão apagada.")
        print("Repete o login e escreve o número de telemóvel.")
        sys.exit(1)
    name = " ".join(x for x in (me.first_name, me.last_name) if x) or "(sem nome)"
    print(f"\nLogin feito: {name} (id {me.id})")
    if getattr(me, "premium", False):
        print("Esta conta tem Telegram Premium: os emojis premium vão funcionar.")
    else:
        print("Esta conta NÃO tem Telegram Premium: os emojis premium serão trocados por emojis normais.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
