"""Login interativo da conta de Telegram (corre uma vez na instalação).

    docker compose run --rm -it bot python -m app.login
"""
import asyncio

from telethon import TelegramClient

from .config import Config


async def main() -> None:
    cfg = Config.load(need_bot=False)
    client = TelegramClient(cfg.user_session, cfg.api_id, cfg.api_hash)
    # pede telemóvel, código e (se existir) a palavra-passe de 2 passos
    await client.start()
    me = await client.get_me()
    name = " ".join(x for x in (me.first_name, me.last_name) if x) or "(sem nome)"
    print(f"\nLogin feito: {name} (id {me.id})")
    if getattr(me, "premium", False):
        print("Esta conta tem Telegram Premium: os emojis premium vão funcionar.")
    else:
        print("Esta conta NÃO tem Telegram Premium: os emojis premium serão trocados por emojis normais.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
