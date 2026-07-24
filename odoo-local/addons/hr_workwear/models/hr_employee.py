from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    personnel_number = fields.Char(string='Табельный номер')
    hire_date = fields.Date(string='Дата приёма')

    workwear_not_required = fields.Boolean(
        string='Спецодежда не положена',
        help='Собственник, директора, офисные сотрудники - исключаются из '
             'расчёта просрочки и экрана "Просрочено", но могут получить '
             'разовую выдачу вручную (п. 10.3 ТЗ).')

    workwear_size_ids = fields.One2many(
        'hr.employee.workwear.size', 'employee_id', string='Размеры спецодежды')
    workwear_issue_ids = fields.One2many(
        'hr.workwear.issue', 'employee_id', string='История выдач')

    workwear_overdue_count = fields.Integer(
        compute='_compute_workwear_due', search='_search_workwear_overdue_count',
        string='Просрочено позиций')
    workwear_next_due_date = fields.Date(
        compute='_compute_workwear_due', string='Ближайшая выдача')
    workwear_last_issue_date = fields.Date(
        compute='_compute_workwear_last_issue_date', string='Дата последней выдачи')

    # Экран "Личный состав" (п. 10.7 ТЗ) хочет размеры пяти основных типов
    # отдельными колонками "через related". Буквальный Odoo related не
    # умеет фильтровать One2many по конкретному типу - это обычный compute
    # поверх уже загруженного (prefetch) workwear_size_ids, без своего
    # батчинга: набор размеров сотрудника всегда маленький (1 запись на
    # тип), лишних запросов на recordset это не добавляет.
    workwear_size_robe = fields.Char(compute='_compute_workwear_size_columns', string='Халат')
    workwear_size_overalls = fields.Char(compute='_compute_workwear_size_columns', string='Комбинезон')
    workwear_size_tshirt = fields.Char(compute='_compute_workwear_size_columns', string='Футболка')
    workwear_size_cap = fields.Char(compute='_compute_workwear_size_columns', string='Кепка')
    workwear_size_jacket = fields.Char(compute='_compute_workwear_size_columns', string='Куртка')

    # Сопоставление со справочником А (purchase.request.department) через
    # ручную связь на purchase.request.department.hr_department_id (п. 10.2
    # ТЗ). НЕ реализовано как штатный related (Odoo related работает только
    # по цепочке вперёд, а тут нужен обратный поиск - "какой отдел закупок
    # ссылается на мой hr.department") - обычный compute с батч-поиском,
    # без store: цена пересчёта на чтение мала, а хранимое поле означало бы
    # либо ручной инвалидатор при правке сопоставления на другой модели,
    # либо риск отстать от него молча (тот же класс риска, что уже описан
    # для vendor_is_possible_duplicate в purchase_vendor_card/NOTES.md).
    purchase_department_id = fields.Many2one(
        'purchase.request.department', compute='_compute_purchase_department_id',
        string='Отдел закупок (сопоставление)')

    def _get_workwear_norm_due_lines(self):
        """Единственное место, где считается дата очередной выдачи по норме
        (п. 10.6 ТЗ) - и карточка сотрудника (_get_workwear_due_stats), и
        экран "Потребность" (hr.workwear.requirement.line) строятся поверх
        этого же метода, чтобы не разойтись в двух местах с одной и той же
        арифметикой (тот же риск, что уже решён батчингом в модуле 8).

        Возвращает список dict: employee (recordset), norm (recordset),
        due_date (date). Одним проходом на весь recordset, а не по одному
        запросу на сотрудника."""
        employees = self.filtered(lambda e: not e.workwear_not_required)
        if not employees:
            return []
        norms = self.env['hr.workwear.norm'].sudo().search([
            '|', ('job_id', 'in', employees.job_id.ids),
                 ('department_id', 'in', employees.department_id.ids),
        ])
        if not norms:
            return []
        rows = self.env['hr.workwear.issue.line'].sudo().read_group(
            [('employee_id', 'in', employees.ids),
             ('type_id', 'in', norms.type_id.ids),
             ('issue_id.state', '!=', 'draft')],
            ['issue_date:max'], ['employee_id', 'type_id'], lazy=False)
        last_issue = {}
        for row in rows:
            emp_id = row['employee_id'][0] if row['employee_id'] else False
            type_id = row['type_id'][0] if row['type_id'] else False
            last_issue[(emp_id, type_id)] = row['issue_date']

        result = []
        for emp in employees:
            applicable = norms.filtered(
                lambda n: (n.job_id and n.job_id == emp.job_id) or
                          (n.department_id and n.department_id == emp.department_id))
            for norm in applicable:
                baseline = last_issue.get((emp.id, norm.type_id.id)) or emp.hire_date
                if not baseline:
                    # Ни выдачи, ни даты приёма - точку отсчёта взять неоткуда,
                    # эта норма-строка не учитывается (не считаем как просрочку,
                    # чтобы не плодить ложные срабатывания при неполных данных).
                    continue
                due_date = baseline + relativedelta(months=norm.period_months)
                result.append({'employee': emp, 'norm': norm, 'due_date': due_date})
        return result

    def _get_workwear_due_stats(self):
        """Агрегат для карточки сотрудника поверх _get_workwear_norm_due_lines."""
        today = fields.Date.context_today(self)
        result = {emp.id: {'overdue': 0, 'next_due': False}
                  for emp in self.filtered(lambda e: not e.workwear_not_required)}
        by_employee = {}
        for line in self._get_workwear_norm_due_lines():
            by_employee.setdefault(line['employee'].id, []).append(line['due_date'])
        for emp_id, due_dates in by_employee.items():
            result[emp_id] = {
                'overdue': sum(1 for d in due_dates if d <= today),
                'next_due': min(due_dates),
            }
        return result

    @api.depends()
    def _compute_workwear_due(self):
        stats = self._get_workwear_due_stats()
        for emp in self:
            data = stats.get(emp.id)
            if emp.workwear_not_required or not data:
                emp.workwear_overdue_count = 0
                emp.workwear_next_due_date = False
            else:
                emp.workwear_overdue_count = data['overdue']
                emp.workwear_next_due_date = data['next_due']

    def _search_workwear_overdue_count(self, operator, value):
        """Позволяет фильтровать "Просрочено" по нехранимому полю (тот же
        приём, что vendor_debt_total в purchase_vendor_card) - батч-проход
        по всем сотрудникам компании, не по одному запросу на человека.
        Сортировка по этому полю при этом не работает - тот же принятый
        размен, что уже задокументирован для денежных полей модуля 8
        (см. NOTES.md)."""
        import operator as py_operator
        ops = {'=': py_operator.eq, '!=': py_operator.ne, '<': py_operator.lt,
               '<=': py_operator.le, '>': py_operator.gt, '>=': py_operator.ge}
        op = ops.get(operator)
        if op is None:
            return [('id', 'in', [])]
        employees = self.search([])
        stats = employees._get_workwear_due_stats()
        matching_ids = [emp.id for emp in employees if op(stats.get(emp.id, {}).get('overdue', 0), value)]
        return [('id', 'in', matching_ids)]

    @api.depends()
    def _compute_workwear_last_issue_date(self):
        rows = self.env['hr.workwear.issue.line'].sudo().read_group(
            [('employee_id', 'in', self.ids)], ['issue_date:max'], ['employee_id'], lazy=False)
        last_by_employee = {row['employee_id'][0]: row['issue_date'] for row in rows if row['employee_id']}
        for emp in self:
            emp.workwear_last_issue_date = last_by_employee.get(emp.id, False)

    @api.depends('workwear_size_ids', 'workwear_size_ids.size_id')
    def _compute_workwear_size_columns(self):
        type_xmlids = {
            'workwear_size_robe': 'hr_workwear.workwear_type_robe',
            'workwear_size_overalls': 'hr_workwear.workwear_type_overalls',
            'workwear_size_tshirt': 'hr_workwear.workwear_type_tshirt',
            'workwear_size_cap': 'hr_workwear.workwear_type_cap',
            'workwear_size_jacket': 'hr_workwear.workwear_type_jacket',
        }
        type_ids = {}
        for field_name, xmlid in type_xmlids.items():
            type_record = self.env.ref(xmlid, raise_if_not_found=False)
            type_ids[field_name] = type_record.id if type_record else False
        for emp in self:
            for field_name, type_id in type_ids.items():
                size = emp.workwear_size_ids.filtered(lambda s, t=type_id: s.type_id.id == t)[:1].size_id
                emp[field_name] = size.name if size else False

    @api.depends()
    def _compute_purchase_department_id(self):
        if not self:
            return
        mapping_rows = self.env['purchase.request.department'].sudo().search_read(
            [('hr_department_id', 'in', self.department_id.ids)], ['id', 'hr_department_id'])
        by_hr_dept = {row['hr_department_id'][0]: row['id'] for row in mapping_rows if row['hr_department_id']}
        for emp in self:
            emp.purchase_department_id = by_hr_dept.get(emp.department_id.id, False)

    def action_view_workwear_issues(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Выдачи спецодежды'),
            'res_model': 'hr.workwear.issue',
            'view_mode': 'list,form',
            'domain': [('employee_id', '=', self.id)],
            'context': {'default_employee_id': self.id},
        }

    def _cron_check_workwear_overdue(self):
        """Ежедневная проверка сроков спецодежды (п. 10.6 ТЗ) - ставит
        активность кладовщику тем же механизмом, что approval/payment
        напоминания в purchase_pdf_import (activity_schedule), не
        изобретает своё уведомление. Не дублирует активность, если она уже
        открыта по этому сотруднику."""
        self.env['hr.workwear.requirement.line']._refresh()
        employees = self.search([('workwear_not_required', '=', False)])
        stats = employees._get_workwear_due_stats()
        keeper_group = self.env.ref('purchase_pdf_import.group_warehouse_keeper', raise_if_not_found=False)
        keepers = keeper_group.users if keeper_group else self.env['res.users']
        summary = _('Просрочена выдача спецодежды')
        for emp in employees:
            data = stats.get(emp.id)
            if not data or data['overdue'] <= 0:
                continue
            already_notified = emp.activity_ids.filtered(lambda a: a.summary == summary)
            if already_notified:
                continue
            for user in keepers:
                emp.activity_schedule(
                    'mail.mail_activity_data_todo',
                    summary=summary,
                    note=_('У сотрудника %(name)s просрочено позиций: %(count)s.') % {
                        'name': emp.name, 'count': data['overdue']},
                    user_id=user.id,
                )
