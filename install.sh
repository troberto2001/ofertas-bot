#!/usr/bin/env bash
# Instalação de um comando: bash install.sh
# Instala o Docker (se faltar), pede as credenciais, faz o login do Telegram e arranca o bot.
# Variáveis opcionais (modo sem perguntas): API_ID API_HASH BOT_TOKEN DEFAULT_CODE INSTALL_DIR
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/ofertas-bot}"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mErro: %s\033[0m\n' "$*" >&2; exit 1; }

ask() { # ask VAR "Pergunta" [secret]
  local var="$1" prompt="$2" secret="${3:-}" val=""
  if [ -n "${!var:-}" ]; then return; fi
  [ -t 0 ] || die "Falta $var e não há terminal para perguntar."
  while [ -z "$val" ]; do
    if [ "$secret" = "secret" ]; then read -rsp "$prompt: " val; echo
    else read -rp "$prompt: " val; fi
  done
  printf -v "$var" '%s' "$val"
}

# ---- 1. privilégios ----
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null || die "Corre como root ou instala o sudo."
  exec sudo -E bash "$0" "$@"
fi

# ---- 2. Docker ----
say "A verificar o Docker"
if ! command -v docker >/dev/null 2>&1; then
  warn "Docker não encontrado. A instalar com o script oficial (get.docker.com)…"
  command -v curl >/dev/null || { apt-get update -y && apt-get install -y curl; }
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || die "Falta o plugin 'docker compose'. Instala docker-compose-plugin."
systemctl enable --now docker >/dev/null 2>&1 || true

# ---- 3. copiar ficheiros ----
say "A instalar em $INSTALL_DIR"
mkdir -p "$INSTALL_DIR/data"
for f in app requirements.txt Dockerfile docker-compose.yml .env.example botctl.sh README.md; do
  rm -rf "${INSTALL_DIR:?}/$f"
  cp -a "$SRC_DIR/$f" "$INSTALL_DIR/$f"
done
chmod +x "$INSTALL_DIR/botctl.sh"
ln -sf "$INSTALL_DIR/botctl.sh" /usr/local/bin/botctl
cd "$INSTALL_DIR"

# ---- 4. configuração ----
if [ -f .env ] && [ -z "${FORCE_ENV:-}" ]; then
  warn "Já existe um .env. Vou mantê-lo (FORCE_ENV=1 para refazer)."
else
  say "Credenciais do Telegram"
  cat <<'TXT'
  1) API_ID e API_HASH: https://my.telegram.org -> "API development tools" -> cria uma app.
  2) BOT_TOKEN: no Telegram, fala com @BotFather -> /newbot (é o teu painel de controlo).
TXT
  ask API_ID "API_ID (número)"
  ask API_HASH "API_HASH" secret
  ask BOT_TOKEN "BOT_TOKEN (do @BotFather)" secret
  DEFAULT_CODE="${DEFAULT_CODE:-}"
  if [ -z "$DEFAULT_CODE" ] && [ -t 0 ]; then
    read -rp "O teu código de desconto (Enter para definir depois com /codigo): " DEFAULT_CODE || true
  fi
  umask 077
  sed -e "s|^API_ID=.*|API_ID=${API_ID}|" \
      -e "s|^API_HASH=.*|API_HASH=${API_HASH}|" \
      -e "s|^BOT_TOKEN=.*|BOT_TOKEN=${BOT_TOKEN}|" \
      -e "s|^DEFAULT_CODE=.*|DEFAULT_CODE=${DEFAULT_CODE}|" \
      .env.example > .env
  chmod 600 .env
fi

# a pasta de dados pertence ao utilizador do contentor (uid 10001)
chown -R 10001:10001 data
chmod 700 data

# ---- 5. construir ----
say "A construir a imagem (1-2 minutos)"
docker compose build

# ---- 6. login ----
if [ -f data/user.session ]; then
  warn "Já existe uma sessão de Telegram. A saltar o login."
else
  say "Login da tua conta de Telegram"
  echo "Vais receber um código no Telegram (e a palavra-passe de 2 passos, se tiveres)."
  [ -t 0 ] || die "O login precisa de um terminal interativo. Corre depois: botctl login"
  docker compose run --rm -it bot python -m app.login
fi
chmod -R go-rwx data || true

# ---- 7. arrancar ----
say "A arrancar o bot"
docker compose up -d
sleep 3
docker compose ps

cat <<'TXT'

Instalado. Agora, no Telegram:
  1. Abre o teu bot (o do @BotFather) e envia /start
  2. /escolher   -> grupos de origem
  3. /destino    -> o teu canal (a tua conta tem de ser admin)
  4. /linkmodelo -> cola um link teu de um produto da loja (gerado na tua conta)
  5. /codigo     -> o teu código de desconto
  6. /modelo     -> texto e emojis (premium incluídos)
  O bot começa em modo MANUAL (aprovas cada oferta). Quando confiares: /auto

Gestão no servidor: botctl status | logs | restart | update | backup | login | uninstall
TXT
