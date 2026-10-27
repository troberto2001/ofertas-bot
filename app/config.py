"""Configuração lida das variáveis de ambiente (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"Valor inválido em {name}: {raw!r} (esperava um número)")


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "sim", "yes", "on")


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    bot_token: str
    data_dir: Path
    owner_id: int | None
    default_code: str
    max_per_hour: int
    min_interval: int
    dedup_hours: int
    idle_alert_hours: int
    require_photo: bool
    mode: str = "web"          # "web" = sem conta (canal público) | "account" = com conta
    web_poll_sec: int = 120
    hacoo_session_key: str = field(default="", repr=False)

    @property
    def user_session(self) -> str:
        return str(self.data_dir / "user")

    @property
    def bot_session(self) -> str:
        return str(self.data_dir / "bot")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bot.db"

    @property
    def rules_path(self) -> Path:
        return self.data_dir / "rules.json"

    @staticmethod
    def load(need_bot: bool = True) -> "Config":
        api_id = _int("API_ID", 0)
        api_hash = os.environ.get("API_HASH", "").strip()
        bot_token = os.environ.get("BOT_TOKEN", "").strip()
        missing = []
        if not api_id:
            missing.append("API_ID")
        if not api_hash:
            missing.append("API_HASH")
        if need_bot and not bot_token:
            missing.append("BOT_TOKEN")
        if missing:
            raise SystemExit("Faltam variáveis no .env: " + ", ".join(missing))

        data_dir = Path(os.environ.get("DATA_DIR", "/data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        owner = _int("OWNER_ID", 0) or None
        mode = os.environ.get("MODE", "web").strip().lower() or "web"
        if mode not in ("web", "account"):
            raise SystemExit("MODE tem de ser 'web' ou 'account'")
        if need_bot and mode == "web" and not owner:
            raise SystemExit(
                "No modo web é preciso OWNER_ID no .env (o teu ID numérico de Telegram: "
                "fala com o @userinfobot)."
            )
        return Config(
            api_id=api_id,
            api_hash=api_hash,
            bot_token=bot_token,
            data_dir=data_dir,
            owner_id=owner,
            default_code=os.environ.get("DEFAULT_CODE", "").strip(),
            max_per_hour=_int("MAX_PER_HOUR", 12),
            min_interval=_int("MIN_INTERVAL_SEC", 20),
            dedup_hours=_int("DEDUP_HOURS", 24),
            idle_alert_hours=_int("IDLE_ALERT_HOURS", 12),
            require_photo=_bool("REQUIRE_PHOTO", True),
            mode=mode,
            web_poll_sec=max(60, _int("WEB_POLL_SEC", 120)),
            hacoo_session_key=os.environ.get("HACOO_SESSION_KEY", "").strip(),
        )
