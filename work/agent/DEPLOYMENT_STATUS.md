# deployment-status (обновлять после каждого деплоя)

Последнее обновление: 2026-07-24 (cloud agent).

## Сервер 78.17.1.240 (тест)

| Проверка | Результат |
|---|---|
| HTTP Odoo `:8069/web/login` | **200** (доступен из интернета) |
| SSH `:22` с cloud agent | **Connection reset** на этапе `kex_exchange_identification` (вероятно firewall/geo/fail2ban; не пароль) |
| Синхронизация addons с git | **Не выполнена автоматически** — нужен SSH с рабочей машины или whitelist IP cloud agent |

### Ожидаемый состав `/opt/odoo/addons/` (из репозитория `odoo-local/addons/`)

- `purchase_pdf_import` (база, обновить перед `hr_workwear`)
- `purchase_vendor_matching`
- `purchase_request_search`
- `purchase_delivery_tracking`
- `purchase_order_archive`
- `purchase_registry_ux`
- `purchase_finance_workspace`
- `purchase_finance_dashboard`
- `purchase_vendor_card`
- `hr_workwear`

### Состояние `ir.module.module` на сервере

**Не снято** (нет SSH). После доступа выполнить:

```bash
cd /opt/odoo
docker compose exec -T odoo odoo shell -d odoo --no-http <<'PY'
mods = env['ir.module.module'].search([('name', 'like', 'purchase_'), '|', ('name', '=', 'hr_workwear'), ('name', '=', 'hr')])
for m in mods.sorted('name'):
    print(m.name, m.state)
PY
```

Скопировать вывод сюда и в memory `deployment-status`.

## Локальная среда (cloud / CI)

- Docker установлен в cloud VM (`docker.io`).
- Дамп `work/db/odoo_20260722.dump` в git **не включён** — для полной приёмки скачать с сервера `/opt/odoo/work/db/` или RESTORE.md.
- Тесты модулей: `scripts/run-all-module-tests.sh` (см. `odoo-local/docker-compose.yml`, порт **8169**).

## Очередь установки на тест/прод (ТЗ 11.1)

1. `purchase_vendor_matching`
2. `purchase_request_search`
3. `purchase_delivery_tracking`
4. `purchase_order_archive`
5. `purchase_registry_ux`
6. `purchase_finance_workspace`
7. `purchase_finance_dashboard`
8. `purchase_vendor_card`
9. `-u purchase_pdf_import` перед `hr_workwear`
10. `hr_workwear`

`hr_workwear` — вне очереди закупок, после обновления базового модуля.
