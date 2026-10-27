#!/usr/bin/env bash
# Accept the flat GitHub upload package and install its files in app/.
set -euo pipefail
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$SRC_DIR"
[ -d app ] && [ -f install.sh ] || { echo "Executa na pasta do repositorio ofertas-bot."; exit 1; }

# Validate every source before changing any file. This also supports a normal
# Git checkout where modules already live under app/.
for file in main.py title_translation.py channel_fill.py webfeed.py; do
  if [ ! -f "$file" ] && [ ! -f "app/$file" ]; then
    echo "Falta $file. Carrega todos os ficheiros do pacote no GitHub."
    exit 1
  fi
done
source_main=app/main.py
[ ! -f main.py ] || source_main=main.py
grep -q 'async def cmd_encher(' "$source_main" || { echo "main.py nao inclui /encher. Carrega a versao nova."; exit 1; }

for file in main.py title_translation.py channel_fill.py webfeed.py; do
  if [ -f "$file" ]; then
    cp -- "$file" "app/$file"
  fi
done
sed -i 's/\r$//' install.sh botctl.sh
# Restore these files even if a previous manual upload mixed up their names.
printf '%s\n' '* text=auto' '*.sh text eol=lf' > .gitattributes
printf '%s\n' '.git' '.env' 'data' '*.enc' 'backup-*.tar.gz' '.venv' \
  '__pycache__' '.pytest_cache' 'test-results' 'playwright-report' > .dockerignore

# install.sh copies to INSTALL_DIR; never let it delete its own source files.
if [ "$SRC_DIR" = "$(realpath -m "${INSTALL_DIR:-/opt/ofertas-bot}")" ]; then
  docker compose build
  docker compose up -d
else
  bash install.sh
fi
docker exec ofertas-bot python -c 'from app.main import App; assert hasattr(App, "cmd_encher") and hasattr(App, "cmd_traducao"); print("Enchimento e traducao instalados. No Telegram: /encher")'
