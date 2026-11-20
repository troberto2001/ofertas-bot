import subprocess
import sys
import textwrap
import time
from pathlib import Path

from app import watchdog
from app.watchdog import Watchdog


def make(tmp_path, connected=True, web=False):
    return Watchdog(tmp_path / "hb", lambda: connected, lambda: web, web_poll_sec=120)


def test_healthy_has_no_problem(tmp_path):
    w = make(tmp_path)
    assert w._problem(time.time()) is None


def test_detects_frozen_loop_disconnection_and_stalled_web(tmp_path):
    now = time.time()
    w = make(tmp_path)
    w.last_tick = now - 500
    assert "ciclo de eventos" in w._problem(now)

    w = make(tmp_path)
    w.last_connected = now - 1000
    assert "sem ligação" in w._problem(now)

    w = make(tmp_path, web=True)
    w.last_web_cycle = now - 5000
    assert "leitura dos canais" in w._problem(now)
    w.web_cycle_done()
    assert w._problem(time.time()) is None

    w = make(tmp_path, web=False)  # sem fontes / em pausa: não conta
    w.last_web_cycle = now - 5000
    assert w._problem(now) is None


def test_heartbeat_file_written_only_when_healthy(tmp_path):
    import asyncio

    async def go(w):
        t = asyncio.create_task(w.run())
        await asyncio.sleep(0.2)
        t.cancel()

    w = make(tmp_path)
    asyncio.run(go(w))
    assert (tmp_path / "hb").exists()

    (tmp_path / "hb").unlink()
    w = make(tmp_path, web=True)
    w.last_web_cycle -= 5000  # problema: não deve escrever a pulsação
    asyncio.run(go(w))
    assert not (tmp_path / "hb").exists()


def test_frozen_process_is_killed_with_exit_code_1(tmp_path):
    """Processo real: o ciclo de eventos bloqueia e o watchdog mata-o (o Docker reinicia)."""
    script = textwrap.dedent(
        f"""
        import asyncio, sys, time
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
        from app import watchdog
        from pathlib import Path
        watchdog.LOOP_STALE_SEC = 1
        w = watchdog.Watchdog(Path({str(tmp_path / 'hb')!r}), lambda: True, lambda: False, 120)
        async def main():
            w.start_thread(check_every=0.2)
            asyncio.create_task(w.run())
            await asyncio.sleep(0.3)
            time.sleep(30)   # bloqueia o ciclo de eventos (bug / pedido preso)
        asyncio.run(main())
        """
    )
    t0 = time.time()
    r = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, timeout=25)
    assert r.returncode == 1
    assert time.time() - t0 < 10  # terminou por causa do watchdog, não ao fim dos 30s
