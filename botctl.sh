#!/usr/bin/env bash
# Gestão do bot no servidor.  Uso: botctl <comando>
set -euo pipefail

# resolve o link simbólico /usr/local/bin/botctl -> pasta real
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
DIR="$(cd "$(dirname "$SELF")" && pwd)"
cd "$DIR"

c() { docker compose "$@"; }

case "${1:-help}" in
  status)   c ps; echo; c logs --tail 15 bot ;;
  logs)     c logs -f --tail 100 bot ;;
  start)    c up -d ;;
  stop)     c stop ;;
  restart)  c restart bot ;;
  update)   # depois de substituires os ficheiros da pasta app/
            c build && c up -d && echo "Atualizado." ;;
  login)    c stop || true
            c run --rm -it bot python -m app.login
            c up -d ;;
  hacoo-key)
            # Generate once, without displaying the key or overwriting an existing one.
            if grep -Eq '^HACOO_SESSION_KEY=.+$' .env; then
              echo "Já existe uma chave Hacoo. Não foi alterada."
              exit 0
            fi
            umask 077
            hacoo_key="$(c run --rm --no-deps -T bot python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
            [[ "$hacoo_key" =~ ^[A-Za-z0-9_-]{43}=$ ]] || { echo "Falhou a geração da chave."; exit 1; }
            sed -i '/^HACOO_SESSION_KEY=/d' .env
            printf '\nHACOO_SESSION_KEY=%s\n' "$hacoo_key" >> .env
            unset hacoo_key
            chmod 600 .env
            c up -d --force-recreate
            echo "Chave guardada em .env. No chat privado do bot, usa /hacoo_login." ;;
  backup)   out="$DIR/backup-$(date +%Y%m%d-%H%M).tar.gz"
            tar -czf "$out" -C "$DIR" data .env
            chmod 600 "$out"
            echo "Backup: $out  (contém sessões Telegram/Hacoo e a chave de cifra: guarda-o em segurança)" ;;
  uninstall)
            c down
            if [ "${2:-}" = "--purge" ]; then
              read -rp "Apagar também sessão e dados? [escreve SIM] " r
              [ "$r" = "SIM" ] && rm -rf "$DIR/data" "$DIR/.env" && echo "Dados apagados."
            else
              echo "Contentor removido. Dados mantidos em $DIR/data (usa --purge para apagar)."
            fi ;;
  *)        cat <<'TXT'
botctl status      estado e últimas linhas do log
botctl logs        log em direto (Ctrl+C para sair)
botctl start|stop|restart
botctl update      reconstruir depois de alterar o código
botctl login       refazer o login do Telegram
botctl hacoo-key   gerar a chave Hacoo uma vez e recriar o contentor
botctl backup      copia de segurança de data/ e .env
botctl uninstall [--purge]
TXT
            ;;
esac
