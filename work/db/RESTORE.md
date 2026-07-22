# Развёртывание локальной копии

Снимок боевой базы на 22.07.2026. Нужен для разработки — половина требований
ТЗ завязана на реальные данные, вслепую их не проверить.

Файлы:
- `odoo_20260722.dump` — база, формат `pg_dump -Fc` (5.4 МБ)
- `filestore_20260722.tar.gz` — вложения: PDF счетов, УПД, платёжки (22 МБ)

---

## Вариант 1: Docker (рекомендуется)

Повторяет боевую конфигурацию. Из каталога, куда скачали комплект:

**1. Подготовить структуру**

```bash
mkdir -p odoo-local/{addons,config,data}
cd odoo-local
cp ../env/docker-compose.local.yml docker-compose.yml
cp ../env/odoo.conf.sample config/odoo.conf
cp -r ../source/purchase_pdf_import addons/
```

Берём именно **`docker-compose.local.yml`** — в нём относительные пути и
отдельные имена контейнеров. Файл `docker-compose.yml` в `env/` — это копия
боевого с абсолютными путями `/opt/odoo/...`, он приложен как справка и
локально не заработает.

**2. Прописать пароли**

В `config/odoo.conf` заменить два `ЗАМЕНИТЬ` на любые локальные значения:

```
db_password = localdev
admin_passwd = localadmin
```

В `docker-compose.yml` заменить `ЗАМЕНИТЬ_ПАРОЛЬ` (встречается дважды) на
**то же самое** значение, что в `db_password`. Если не совпадут — Odoo не
подключится к базе.

**3. Поднять контейнеры**

```bash
docker compose up -d
docker compose ps
```

**4. Создать пустую базу и залить дамп**

```bash
docker compose exec db createdb -U odoo odoo
docker compose exec -T db pg_restore -U odoo -d odoo --no-owner --role=odoo < ../db/odoo_20260722.dump
```

`--no-owner` обязателен: на боевом владельцем объектов может быть другая роль.
Предупреждения об уже существующих расширениях — нормально.

**5. Распаковать filestore**

```bash
tar -xzf ../db/filestore_20260722.tar.gz -C data/
chown -R 100:101 data/filestore
```

**Каталог именно `data/filestore/odoo`** — при `data_dir = /var/lib/odoo`
Odoo ищет вложения там. Ровно на этом на боевом сервере уже обожглись:
архив распаковали в `.local/share/Odoo/filestore`, и 548 из 554 вложений
перестали открываться.

**6. Перезапустить и войти**

```bash
docker compose restart odoo
```

http://localhost:8069 — логин `admin`, пароль от боевой базы (дамп содержит
боевые учётные записи).

---

## Вариант 2: Odoo из исходников

Если работаете без Docker:

```bash
createdb odoo
pg_restore -U <user> -d odoo --no-owner odoo_20260722.dump
tar -xzf filestore_20260722.tar.gz -C ~/.local/share/Odoo/
```

Здесь filestore идёт в `~/.local/share/Odoo/filestore/odoo` — это путь
по умолчанию, когда `data_dir` в конфиге **не задан**. Если задаёте
`data_dir` явно — кладите в `<data_dir>/filestore/odoo`.

Запуск:
```bash
./odoo-bin -d odoo --addons-path=addons,../work/source -c odoo.conf
```

---

## Проверка, что всё встало

```bash
# 32 закупки, 46 заявок
docker compose exec db psql -U odoo -d odoo -c \
  "SELECT (SELECT count(*) FROM purchase_order) AS закупки,
          (SELECT count(*) FROM purchase_request) AS заявки,
          (SELECT count(*) FROM res_partner WHERE active) AS контрагенты;"

# вложения: в БД 554 записи, на диске должно быть 527 файлов
docker compose exec odoo sh -c "find /var/lib/odoo/filestore/odoo -type f | wc -l"
```

Ожидаемо: закупки 32, заявки 46, контрагенты 39, файлов 527.

В интерфейсе открыть любую закупку и убедиться, что PDF счёта открывается.
Если `FileNotFoundError` — filestore лежит не в том каталоге, см. шаг 5.

---

## Команды разработки

```bash
# установить новый модуль
docker compose exec odoo odoo -d odoo -i <module> --stop-after-init

# обновить после правок
docker compose exec odoo odoo -d odoo -u <module> --stop-after-init

# прогнать тесты
docker compose exec odoo odoo -d odoo -u <module> --test-enable --stop-after-init

# логи
docker compose logs -f odoo
```

---

## Важное про работу с этой копией

- **ИИ-импорт PDF работать не будет** — API-ключ Anthropic в настройках
  не задан (таблица `purchase_pdf_import_settings` пуста). Для разработки
  модулей из ТЗ это не мешает: ни один из них импорт не затрагивает,
  кроме модуля 8, где правится только поиск поставщика — его тестировать
  юнит-тестами, без реальных вызовов API.
- **В базе боевые персональные данные** — ФИО, телефоны, почта сотрудников
  и контрагентов. Копию держать локально, никуда не выкладывать.
- Дамп снят **после** починки filestore, то есть уже корректный.
