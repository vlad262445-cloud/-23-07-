# Работа на вашем ПК (без Cloud Agent)

Агент и терминалы выполняются **на вашем компьютере** в Cursor Desktop. История чатов остаётся на этом ПК (это нормально для вашего сценария).

---

## 1. Что установить один раз

1. **Git** — [https://git-scm.com/](https://git-scm.com/)
2. **Cursor Desktop** — [https://cursor.com/download](https://cursor.com/download) (не только браузер)
3. **Docker Desktop** (если хотите Odoo локально) — [https://www.docker.com/products/docker-desktop/](https://www.docker.com/products/docker-desktop/)
4. **OpenSSH** — в Windows 10/11: «Параметры → Приложения → Дополнительные компоненты → OpenSSH Client»

Пароли сервера храните в менеджере паролей, **не в репозитории**.

---

## 2. Склонировать проект

```bash
git clone <URL-вашего-репозитория> odoo18-tz
cd odoo18-tz
```

Откройте в Cursor: **File → Open Folder** → каталог `odoo18-tz`.

В чате Agent выберите режим **Local** (не Cloud), если в списке агента есть переключатель.

---

## 3. Два рабочих варианта

### Вариант A — правки сразу на тестовом сервере (рекомендуется)

У вас SSH к `78.17.1.240` с ПК обычно работает (с Cloud Agent — нет).

1. Cursor: **Remote SSH** к серверу  
   - Установите расширение **Remote - SSH** (если ещё нет).  
   - `F1` → **Remote-SSH: Connect to Host** → `root@78.17.1.240`  
   - Откройте папку **`/opt/odoo`** (или где лежит проект).

2. Проверка, что код актуален:

```bash
ls -1 /opt/odoo/addons/
cd /opt/odoo && docker compose ps
```

3. Список установленных модулей:

```bash
cd /opt/odoo
docker compose exec -T odoo odoo shell -d odoo --no-http <<'PY'
for m in env['ir.module.module'].search([('name','like','purchase_')]).sorted('name'):
    print(m.name, m.state)
for name in ('hr','hr_workwear'):
    m = env['ir.module.module'].search([('name','=',name)], limit=1)
    if m: print(m.name, m.state)
PY
```

4. Если на сервере **старые файлы**, а в git новее — с вашего ПК (из клона репозитория):

```bash
export SSHPASS='...'   # или используйте ssh-ключ
bash scripts/sync-addons-to-server.sh
```

Либо вручную: `rsync` / `scp` каталога `odoo-local/addons/` → `/opt/odoo/addons/`.

**Агент в Cursor** в этой схеме: чат на ПК, команды — на сервере через Remote SSH.

---

### Вариант B — Odoo только локально (Docker)

Код модулей: `odoo-local/addons/` (уже в репозитории).

```bash
cd odoo-local
docker compose up -d --build
```

Порт **8169** (см. `odoo-local/docker-compose.yml`), чтобы не конфликтовать с другими Odoo.

Дамп боевой базы **не в git** — скачайте с сервера (если нужны 32 закупки):

```bash
scp root@78.17.1.240:/opt/odoo/work/db/odoo_20260722.dump work/db/
scp root@78.17.1.240:/opt/odoo/work/db/filestore_20260722.tar.gz work/db/
```

Дальше — [work/db/RESTORE.md](db/RESTORE.md).

Тесты модулей (когда контейнер запущен):

```bash
bash scripts/run-all-module-tests.sh
```

---

## 4. Что сказать агенту в первом сообщении

Скопируйте и подставьте свой вариант (A или B):

```
Работаем локально на моём ПК, не Cloud Agent.
Проект Odoo 18, модули в odoo-local/addons/.
ТЗ: work/TZ_purchase_modules.md, краткие правила: .cursor/rules/odoo-project.mdc
Сервер тест: 78.17.1.240, путь /opt/odoo (SSH с этого ПК работает).
Задача: ...
```

---

## 5. Синхронизация кода ПК ↔ сервер

| Действие | Команда |
|----------|---------|
| Отправить addons на сервер | `bash scripts/sync-addons-to-server.sh` |
| Установить модули по очереди ТЗ | на сервере: `bash scripts/install-modules-order.sh` |
| Статус деплоя (заполнять руками) | [work/agent/DEPLOYMENT_STATUS.md](agent/DEPLOYMENT_STATUS.md) |

---

## 6. Чего не делать

- Не включать Cloud Agent, если нужен простой SSH с ПК — достаточно **Local** + **Remote SSH**.
- Не коммитить пароли и `work/db/*.dump` (они в `.gitignore`).
- Не ждать, что тот же чат откроется на втором ПК — на втором ПК тот же репозиторий + новый чат или Remote SSH к тому же серверу.

---

## 7. Если Remote SSH не подключается

На **вашем** ПК в терминале:

```bash
ssh -v root@78.17.1.240
```

Если здесь OK, а в Cursor нет — проверьте `~/.ssh/config` и что Cursor использует тот же ключ. Если и здесь reset — смотрите firewall/fail2ban на сервере (разрешить ваш домашний IP).
