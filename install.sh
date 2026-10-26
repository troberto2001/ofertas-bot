#!/usr/bin/env bash
# Instalação de um comando: bash install.sh
# Instala o Docker (se faltar), pede as credenciais, e arranca o bot.
# Modo web (por omissão): sem login de conta. Modo account: faz o login do Telegram.
# Variáveis opcionais (sem perguntas): API_ID API_HASH BOT_TOKEN OWNER_ID MODE DEFAULT_CODE INSTALL_DIR
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
for f in app requirements.txt Dockerfile docker-compose.yml botctl.sh README.md; do
  [ -e "$SRC_DIR/$f" ] || die "Falta o ficheiro $f em $SRC_DIR"
  rm -rf "${INSTALL_DIR:?}/$f"
  cp -a "$SRC_DIR/$f" "$INSTALL_DIR/$f"
done
chmod +x "$INSTALL_DIR/botctl.sh"
ln -sf "$INSTALL_DIR/botctl.sh" /usr/local/bin/botctl
cd "$INSTALL_DIR"

# ---- 4. configuração ----
if [ -f .env ] && [ -z "${FORCE_ENV:-}" ]; then
  warn "Já existe um .env. Vou mantê-lo (FORCE_ENV=1 para refazer)."
  grep -q '^MODE=' .env || { echo "MODE=web" >> .env; warn "Acrescentei MODE=web ao .env."; }
else
  say "Configuração"
  cat <<'TXT'
  1) API_ID e API_HASH: https://my.telegram.org -> "API development tools" -> cria uma app.
  2) BOT_TOKEN: no Telegram, fala com @BotFather -> /newbot (é o teu painel de controlo).
  3) OWNER_ID: o teu ID numérico (fala com o @userinfobot). O bot só responde a ti.
TXT
  ask API_ID "API_ID (número)"
  ask API_HASH "API_HASH" secret
  ask BOT_TOKEN "BOT_TOKEN (do @BotFather)" secret
  ask OWNER_ID "OWNER_ID (o teu ID numérico)"
  if [ -z "${MODE:-}" ]; then
    if [ -t 0 ]; then
      echo
      echo "Modo de leitura:"
      echo "  1) web      - SEM conta. Lê canais públicos pela web. (recomendado)"
      echo "  2) account  - com a tua conta (grupos privados, emojis premium)."
      read -rp "Escolhe [1]: " m || true
      case "${m:-1}" in 2) MODE=account ;; *) MODE=web ;; esac
    else
      MODE=web
    fi
  fi
  DEFAULT_CODE="${DEFAULT_CODE:-}"
  if [ -z "$DEFAULT_CODE" ] && [ -t 0 ]; then
    read -rp "O teu código de desconto (Enter para definir depois com /codigo): " DEFAULT_CODE || true
  fi
  umask 077
  {
    echo "API_ID=$(printf '%s' "$API_ID" | tr -cd '0-9')"
    echo "API_HASH=$(printf '%s' "$API_HASH" | tr -cd '0-9a-fA-F')"
    echo "BOT_TOKEN=$(printf '%s' "$BOT_TOKEN" | tr -d ' \r\n')"
    echo "MODE=$MODE"
    echo "OWNER_ID=$(printf '%s' "$OWNER_ID" | tr -cd '0-9')"
    echo "DEFAULT_CODE=$(printf '%s' "$DEFAULT_CODE" | tr -d '\r\n')"
    echo "MAX_PER_HOUR=12"
    echo "MIN_INTERVAL_SEC=20"
    echo "DEDUP_HOURS=24"
    echo "IDLE_ALERT_HOURS=12"
    echo "REQUIRE_PHOTO=true"
    echo "WEB_POLL_SEC=120"
  } > .env
  chmod 600 .env
fi
MODE_NOW="$(grep -E '^MODE=' .env | tail -1 | cut -d= -f2)"

# validação rápida (não mostra segredos)
api_hash_len=$(grep -E '^API_HASH=' .env | tail -1 | cut -d= -f2 | tr -d '\r\n' | wc -c)
[ "$api_hash_len" -eq 32 ] || warn "Atenção: o API_HASH não tem 32 caracteres (tem $api_hash_len). Confirma-o em my.telegram.org."

# a pasta de dados pertence ao utilizador do contentor (uid 10001)
chown -R 10001:10001 data
chmod 700 data

# ---- 5. construir ----
say "A construir a imagem (1-2 minutos)"
docker compose build

# ---- 6. login (só modo account) ----
if [ "$MODE_NOW" = "account" ]; then
  say "Login da tua conta de Telegram"
  echo "Escreve o teu NÚMERO DE TELEMÓVEL (+351...), não o token do bot."
  echo "(Se a conta já estiver autenticada, não pergunta nada.)"
  [ -t 0 ] || die "O login precisa de um terminal interativo. Corre depois: botctl login"
  docker compose run --rm -it bot python -m app.login
fi
chmod -R go-rwx data || true

# ---- 7. arrancar ----
say "A arrancar o bot"
docker compose up -d
sleep 3
docker compose ps

if [ "$MODE_NOW" = "account" ]; then
cat <<'TXT'

Instalado (modo com conta). No Telegram, no teu bot (@BotFather):
  1. /start
  2. /escolher   -> grupos de origem          3. /destino -> o teu canal
  4. /linkmodelo -> um link teu de um produto  5. /codigo  -> o teu código
  O bot começa em modo MANUAL (aprovas cada oferta). Quando confiares: /auto
TXT
else
cat <<'TXT'

Instalado (modo web, sem conta). Faz isto no Telegram:
  1. Adiciona o teu bot como ADMINISTRADOR do teu canal (permissão de publicar).
  2. Abre o bot e envia /start
  3. /adicionar t.me/nomedocanal   -> canal PÚBLICO de origem
  4. /destino @teucanal            -> o teu canal
  5. /linkmodelo LINK              -> um link teu de um produto da loja
  6. /codigo CODIGO                -> o teu código
  O bot começa em modo MANUAL (aprovas cada oferta). Quando confiares: /auto
TXT
fi
echo
echo "Gestão no servidor: botctl status | logs | restart | update | backup | login | uninstall"
