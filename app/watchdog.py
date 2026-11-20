"""Vigilância do próprio bot: se bloquear, perder a ligação ou parar de ler as
fontes, o processo termina e o Docker (restart: unless-stopped) volta a levantá-lo
de imediato. Também escreve um ficheiro de pulsação que o healthcheck do Docker lê.

Casos que o Docker, sozinho, não apanha: o processo continua "vivo" mas está preso.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("ofertas.watchdog")

LOOP_STALE_SEC = 120        # ciclo de eventos parado
DISCONNECTED_SEC = 300      # sem ligação ao Telegram
WEB_STALE_SEC = 900         # modo web sem concluir nenhuma volta de leitura


class Watchdog:
    def __init__(self, heartbeat_file: Path, is_connected: Callable[[], bool],
                 web_expected: Callable[[], bool], web_poll_sec: int):
        self.file = heartbeat_file
        self.is_connected = is_connected
        self.web_expected = web_expected
        self.web_stale = max(WEB_STALE_SEC, web_poll_sec * 5)
        now = time.time()
        self.last_tick = now
        self.last_connected = now
        self.last_web_cycle = now

    # chamado pelo web_poller no fim de cada volta completa
    def web_cycle_done(self) -> None:
        self.last_web_cycle = time.time()

    async def run(self) -> None:
        """Corre no ciclo de eventos: se este ciclo parar, a thread de baixo dá por isso."""
        while True:
            now = time.time()
            self.last_tick = now
            try:
                if self.is_connected():
                    self.last_connected = now
            except Exception:
                pass
            if self._problem(now) is None:
                try:
                    self.file.write_text(str(int(now)))
                except OSError:
                    pass
            await asyncio.sleep(15)

    def _problem(self, now: float) -> str | None:
        if now - self.last_tick > LOOP_STALE_SEC:
            return f"ciclo de eventos parado há {int(now - self.last_tick)}s"
        if now - self.last_connected > DISCONNECTED_SEC:
            return f"sem ligação ao Telegram há {int(now - self.last_connected)}s"
        if self.web_expected() and now - self.last_web_cycle > self.web_stale:
            return f"leitura dos canais parada há {int(now - self.last_web_cycle)}s"
        return None

    def start_thread(self, check_every: float = 10) -> None:
        def loop() -> None:
            while True:
                time.sleep(check_every)
                problem = self._problem(time.time())
                if problem:
                    log.critical("Watchdog: %s. A reiniciar o processo.", problem)
                    logging.shutdown()
                    os._exit(1)  # o Docker levanta-o logo (restart: unless-stopped)

        threading.Thread(target=loop, name="watchdog", daemon=True).start()
