#!/usr/bin/env bash
# Прогон odoo --test-enable для всех модулей ТЗ (локально или на сервере в docker).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="${ROOT}/odoo-local/docker-compose.yml"
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
  hr_workwear
)

run_odoo() {
  if [[ -f "$COMPOSE" ]] && docker compose -f "$COMPOSE" ps odoo 2>/dev/null | grep -q Up; then
    docker compose -f "$COMPOSE" exec -T odoo odoo -d "$DB" "$@"
  elif [[ -d /opt/odoo ]]; then
    cd /opt/odoo && docker compose exec -T odoo odoo -d "$DB" "$@"
  else
    echo "No running Odoo (odoo-local compose or /opt/odoo)" >&2
    exit 1
  fi
}

FAILED=()
for m in "${MODULES[@]}"; do
  echo "======== TEST $m ========"
  if run_odoo -u "$m" --test-enable --stop-after-init; then
    echo "OK $m"
  else
    echo "FAIL $m" >&2
    FAILED+=("$m")
  fi
done

if ((${#FAILED[@]})); then
  echo "Failed: ${FAILED[*]}" >&2
  exit 1
fi
echo "All module tests passed."
