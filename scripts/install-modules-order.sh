#!/usr/bin/env bash
# Установка/обновление модулей по очереди ТЗ 11.1 на сервере (внутри docker odoo).
# Использование на сервере: bash install-modules-order.sh
# Или: ssh root@HOST 'bash -s' < scripts/install-modules-order.sh
set -euo pipefail

ODOO_DIR="${ODOO_DIR:-/opt/odoo}"
DB="${ODOO_DB:-odoo}"

MODULES=(
  purchase_vendor_matching
  purchase_request_search
  purchase_delivery_tracking
  purchase_order_archive
  purchase_registry_ux
  purchase_finance_workspace
  purchase_finance_dashboard
  purchase_vendor_card
)

cd "$ODOO_DIR"

echo "=== Update base purchase_pdf_import (required before hr_workwear) ==="
docker compose exec -T odoo odoo -d "$DB" -u purchase_pdf_import --stop-after-init

for m in "${MODULES[@]}"; do
  echo "=== Install/update $m ==="
  docker compose exec -T odoo odoo -d "$DB" -i "$m" --stop-after-init
done

echo "=== hr_workwear (optional, installs hr) ==="
docker compose exec -T odoo odoo -d "$DB" -i hr_workwear --stop-after-init

echo "All modules processed."
