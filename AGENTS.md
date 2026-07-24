# AGENTS.md

## Cursor Cloud specific instructions

This repo is a set of **Odoo 18** procurement/purchasing addons (Python + OWL/JS),
run via **Docker Compose**. There is no Python venv, `requirements.txt`, or JS
package manager — all runtime deps are baked into `odoo-local/Dockerfile`
(pip: `pdfplumber`, `pymupdf`, `anthropic`, `openai`, `httpx`) and declared per
module in `__manifest__.py`. The runnable stack lives in `odoo-local/`
(`docker-compose.yml` + `Dockerfile` + `config/odoo.conf`, project name
`odoo18tz`). Standard dev/test commands are documented in
`work/db/RESTORE.md` ("Команды разработки") — refer to it rather than
re-deriving them.

### Services (both MUST run)

| Service | Container | Notes |
|---|---|---|
| PostgreSQL 16 | `odoo18tz_db` | Odoo DB backend. |
| Odoo 18 app | `odoo18tz_odoo` | Web on host port **8169** (→8069), longpolling **8172** (→8072). |

The Anthropic/OpenAI AI PDF-import feature is optional and inert without an API
key (the settings table is empty by design); it does not block anything else.

### Starting the stack (Docker daemon must be running)

- Docker Engine is installed at the system level but there is **no systemd** in
  this VM, so `dockerd` must be started manually if not already running:
  `sudo dockerd >/tmp/dockerd.log 2>&1 &` (wait until `sudo docker info` works).
  Use `sudo docker` (the daemon socket is root-owned).
- Bring up the stack: `cd odoo-local && sudo docker compose up -d`.
- The `./data` bind mount (→ `/var/lib/odoo`) must be writable by the container's
  `odoo` user (**uid 100 / gid 101**). If you recreate `data/` or hit
  `PermissionError: /var/lib/odoo/filestore` on startup, run
  `sudo chown -R 100:101 odoo-local/data` and restart the `odoo` service.

### Database initialization (fresh DB — the prod dump is gitignored/absent)

`work/db/*.dump` and `*.tar.gz` (the prod snapshot referenced in
`work/db/RESTORE.md`) are **not** committed, so init a fresh DB instead. The
base module references the `contacts` app, so it must be installed too. From
`odoo-local/` with the daemon up:

```
sudo docker compose stop odoo
sudo docker compose exec -T db psql -U odoo -d postgres -c "DROP DATABASE IF EXISTS odoo;"
sudo docker compose exec -T db createdb -U odoo odoo
sudo docker compose run --rm odoo odoo -d odoo \
  -i contacts,purchase_pdf_import,purchase_vendor_matching,purchase_request_search,purchase_delivery_tracking,purchase_order_archive,purchase_registry_ux,purchase_finance_workspace,purchase_finance_dashboard,purchase_vendor_card,hr_workwear \
  --stop-after-init
sudo docker compose start odoo
```

Web UI: `http://localhost:8169`, login `admin` / password `admin` on a
fresh DB. UI is in Russian.

### Running tests

Run one-off with the server stopped to avoid registry contention, e.g.:
`sudo docker compose run --rm odoo odoo -d odoo -u <modules> --test-enable --stop-after-init`.

- **Non-obvious gotcha:** the extension modules' `post_install` tests
  (`purchase_vendor_card`, `purchase_finance_workspace`, `purchase_finance_dashboard`,
  `purchase_registry_ux`, `purchase_order_archive`) call
  `order.action_send_to_approval()`, which raises
  *"Добавьте хотя бы одного согласующего…"* unless at least one user is in the
  `purchase_pdf_import.group_chief_buyer` group. The base `purchase_pdf_import`
  tests seed their own chief buyer (its 90 tests pass on a bare fresh DB), but
  the extension test classes rely on such a user already existing in the DB
  (normally supplied by the prod snapshot). On a fresh DB, seed one first via
  `odoo shell`:

  ```
  sudo docker compose run --rm odoo odoo shell -d odoo --no-http <<'PY'
  env['res.users'].create({'name':'Chief Buyer','login':'chief_buyer',
      'groups_id':[(4, env.ref('purchase_pdf_import.group_chief_buyer').id)]})
  env.cr.commit()
  PY
  ```

  With that user present, all extension tests pass (verified: 0 failed, 0 errors).
