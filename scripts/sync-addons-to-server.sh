#!/usr/bin/env bash
# Синхронизация addons на тестовый сервер. Запуск с машины, где SSH к 78.17.1.240 работает.
# Пароль: SSHPASS или ключ. Не хранить пароль в репозитории.
set -euo pipefail

HOST="${ODOO_SERVER_HOST:-78.17.1.240}"
USER="${ODOO_SERVER_USER:-root}"
REMOTE_ADDONS="${ODOO_REMOTE_ADDONS:-/opt/odoo/addons}"
SRC="$(cd "$(dirname "$0")/../odoo-local/addons" && pwd)"

RSYNC_SSH="ssh -o StrictHostKeyChecking=no"
if [[ -n "${SSHPASS:-}" ]] && command -v sshpass >/dev/null; then
  RSYNC_SSH="sshpass -e ssh -o StrictHostKeyChecking=no"
fi

echo "Sync $SRC -> ${USER}@${HOST}:${REMOTE_ADDONS}/"
rsync -avz --delete -e "$RSYNC_SSH" \
  "$SRC/" "${USER}@${HOST}:${REMOTE_ADDONS}/"

echo "Restart Odoo..."
$RSYNC_SSH "${USER}@${HOST}" "cd /opt/odoo && docker compose restart odoo"

echo "Done. Update work/agent/DEPLOYMENT_STATUS.md with module states."
