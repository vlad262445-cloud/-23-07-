from . import models

# Реестр личного состава (data/personnel_roster.csv), п. 10.2/10.3 ТЗ - login
# -> справочник Б (hr.department), должность/телефон/почта из реестра,
# сверено построчно с реестром при написании модуля (см. NOTES.md). Значения
# "как решено заказчиком 22.07.2026" - реестр уже содержит исправленные
# значения (Мицуков/Кустанович/Наточисел), просто переносятся сюда.
_KNOWN_LOGIN_ROSTER = {
    'asadullin@odoo': {
        'department': 'Производство', 'job_title': 'Инженер по ПП',
        'work_phone': '8-996-104-32-14', 'work_email': False,
    },
    'borovik@odoo': {
        'department': 'Производство', 'job_title': 'Нач. производства',
        'work_phone': '8-921-559-03-89', 'work_email': 'factory@cgnl.ru',
    },
    'gibieva@odoo': {
        'department': 'Управление', 'job_title': 'Ген. Дир',
        'work_phone': '8-931-251-83-83', 'work_email': 'gendir@cgnl.ru',
    },
    'chutchikov@odoo': {
        'department': False, 'job_title': 'Нач. сборки оружия',
        'work_phone': '8-921-887-11-87', 'work_email': 'weapon@cgnl.ru',
    },
    'skrynik@odoo': {
        'department': 'Инстр. Кладовая', 'job_title': 'Кладовщик-снабженец',
        'work_phone': '8-909-561-52-65', 'work_email': 'skr@cgnl.ru',
    },
    'mitsukov@odoo': {
        'department': 'Токарные ЧПУ', 'job_title': 'Нач. Ток уч-ка',
        'work_phone': '8-987-613-93-43', 'work_email': False,
    },
    'romanova@odoo': {
        'department': 'Управление', 'job_title': 'Гл. бухгалтер',
        'work_phone': '8-911-103-34-37', 'work_email': 'glbuh@cgnl.ru',
    },
    'svyatchenko@odoo': {
        'department': 'Производство', 'job_title': 'Оператор 1С',
        'work_phone': '8-981-792-90-58', 'work_email': 'operator1c@custom-guns.ru',
    },
    'kiselev@odoo': {
        'department': 'ОТК', 'job_title': 'Начальник ОТК',
        'work_phone': '8-981-749-64-92', 'work_email': 'kis@cgnl.ru',
    },
    'kustanovich@odoo': {
        'department': 'Инженерный отдел', 'job_title': 'Гл. Конструктор',
        'work_phone': '8-921-775-21-81', 'work_email': False,
    },
    'natochisel@odoo': {
        'department': 'Управление', 'job_title': 'Директор по продажам оружия',
        'work_phone': '8-921-361-10-83', 'work_email': 'alexeyn@cgnl.ru',
    },
    'fedchin@odoo': {
        'department': 'Управление', 'job_title': 'Собственник',
        'work_phone': False, 'work_email': False, 'workwear_not_required': True,
    },
}

_EXCLUDED_LOGINS = {'__system__', 'default', 'public', 'portaltemplate'}


def _fix_department_dictionary_a(env):
    """Правки в справочнике А (purchase.request.department), согласованные
    с заказчиком 22.07.2026 (п. 10.2 ТЗ) - переименование существующей
    записи (не создание новой - на неё уже могут ссылаться заявки
    Кустановича), и смена отдела Наточисела. Идемпотентно - повторная
    установка ничего не задваивает и не переписывает поверх."""
    Department = env['purchase.request.department']
    old = Department.search([('name', '=', 'Технологический отдел')], limit=1)
    if old:
        old.name = 'Инженерный отдел'

    natochisel = env['res.users'].search([('login', '=', 'natochisel@odoo')], limit=1)
    if natochisel and natochisel.purchase_department_id.name == 'Продажи':
        upravlenie = Department.search([('name', '=', 'Отдел управления')], limit=1)
        if upravlenie:
            natochisel.purchase_department_id = upravlenie.id


def _migrate_res_users_department_column(env):
    """Установка hr рядом с purchase_pdf_import обнажает конфликт имён:
    оба модуля заводили res.users.department_id (см. models/res_users.py в
    purchase_pdf_import) - Odoo молча склеивала два определения в одно
    битое поле вместо ошибки при загрузке. Поле в purchase_pdf_import
    переименовано в purchase_department_id, но старая физическая колонка
    department_id (5 реальных значений) остаётся в таблице как есть -
    Odoo никогда не удаляет колонки сама. Копируем данные один раз сырым
    SQL, идемпотентно (WHERE ... IS NULL с обеих сторон)."""
    env.cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'res_users' AND column_name = 'department_id'
    """)
    if not env.cr.fetchone():
        return
    env.cr.execute("""
        UPDATE res_users SET purchase_department_id = department_id
        WHERE department_id IS NOT NULL AND purchase_department_id IS NULL
    """)
    env.invalidate_all()


def _create_employees_for_active_users(env):
    """post_init_hook (п. 10.3 ТЗ) - hr.employee для каждого активного
    res.users, кроме служебных логинов. Идемпотентно - проверка по
    user_id перед созданием."""
    HrEmployee = env['hr.employee']
    HrDepartment = env['hr.department']
    users = env['res.users'].search([('active', '=', True)])
    for user in users:
        if user.login in _EXCLUDED_LOGINS:
            continue
        if HrEmployee.search([('user_id', '=', user.id)], limit=1):
            continue
        roster = _KNOWN_LOGIN_ROSTER.get(user.login, {})
        department = False
        if roster.get('department'):
            department = HrDepartment.search([('name', '=', roster['department'])], limit=1)
        HrEmployee.create({
            'name': user.name,
            'user_id': user.id,
            'department_id': department.id if department else False,
            'job_title': roster.get('job_title', False),
            'work_phone': roster.get('work_phone', False),
            'work_email': roster.get('work_email', False),
            'workwear_not_required': roster.get('workwear_not_required', False),
        })
    # Технический логин ревью/администратора - не реальный сотрудник, не
    # должен засорять экран "Просрочено" с первого дня (п. 10.3 ТЗ -
    # такое же рассуждение, что уже привело к полю workwear_not_required
    # для Федчина, просто применено к ещё одной нереальной "персоне").
    admin_employee = HrEmployee.search([('user_id.login', '=', 'admin')], limit=1)
    if admin_employee and not admin_employee.workwear_not_required:
        admin_employee.workwear_not_required = True


def post_init_hook(env):
    # Порядок важен: миграция колонки должна отработать ДО правок
    # справочника А, иначе naochisel.purchase_department_id ещё пуст.
    _migrate_res_users_department_column(env)
    _fix_department_dictionary_a(env)
    _create_employees_for_active_users(env)
