from datetime import date

from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, tagged


class WorkwearTestMixin:

    @classmethod
    def _make_type(cls, name, scale='alpha', period=6):
        return cls.env['hr.workwear.type'].create({
            'name': name, 'size_scale': scale, 'wear_period_months': period,
        })

    @classmethod
    def _make_size(cls, name, scale):
        return cls.env['hr.workwear.size'].create({'name': name, 'scale': scale})

    @classmethod
    def _make_employee(cls, name, **vals):
        vals.setdefault('name', name)
        return cls.env['hr.employee'].create(vals)


@tagged('post_install', '-at_install')
class TestWorkwearIssue(WorkwearTestMixin, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.type_robe = cls._make_type('Test Robe', 'alpha', period=12)
        cls.size_m = cls._make_size('Test M', 'alpha')
        cls.size_l = cls._make_size('Test L', 'alpha')
        cls.employee = cls._make_employee('Test Employee Issue')

    def test_size_autofill_from_employee_card(self):
        """п. 10.5/10.10 ТЗ - размер подставляется автоматически из карточки
        сотрудника при выборе типа в строке выдачи."""
        self.employee.workwear_size_ids = [(0, 0, {'type_id': self.type_robe.id, 'size_id': self.size_l.id})]
        issue = self.env['hr.workwear.issue'].create({'employee_id': self.employee.id})
        line = self.env['hr.workwear.issue.line'].new({'issue_id': issue.id})
        line.type_id = self.type_robe
        line._onchange_type_id()
        self.assertEqual(line.size_id, self.size_l)

    def test_missing_size_gives_warning_not_blocking(self):
        """п. 10.5 ТЗ - если размер у сотрудника не заполнен, выдать можно,
        онченж только предупреждает, не блокирует сохранение."""
        employee_no_size = self._make_employee('Test Employee No Size')
        issue = self.env['hr.workwear.issue'].create({'employee_id': employee_no_size.id})
        line = self.env['hr.workwear.issue.line'].new({'issue_id': issue.id})
        line.type_id = self.type_robe
        result = line._onchange_type_id()
        self.assertFalse(line.size_id)
        self.assertIn('warning', result)
        # Сохранение без размера должно пройти без исключений.
        saved_issue = self.env['hr.workwear.issue'].create({
            'employee_id': employee_no_size.id,
            'line_ids': [(0, 0, {'type_id': self.type_robe.id, 'quantity': 1})],
        })
        self.assertTrue(saved_issue.line_ids)

    def test_expiry_date_from_issue_date_and_period(self):
        """п. 10.5/10.10 ТЗ - expiry_date = issue_date + срок носки, и
        пересчитывается при смене срока."""
        issue = self.env['hr.workwear.issue'].create({
            'employee_id': self.employee.id,
            'issue_date': date(2026, 1, 15),
            'line_ids': [(0, 0, {
                'type_id': self.type_robe.id, 'wear_period_months': 12, 'quantity': 1,
            })],
        })
        line = issue.line_ids
        self.assertEqual(line.expiry_date, date(2027, 1, 15))
        line.wear_period_months = 6
        self.assertEqual(line.expiry_date, date(2026, 7, 15))

    def test_unique_employee_type_constraint(self):
        """п. 10.4/10.10 ТЗ - у одного сотрудника не может быть двух
        размеров одного типа."""
        self.env['hr.employee.workwear.size'].create({
            'employee_id': self.employee.id, 'type_id': self.type_robe.id, 'size_id': self.size_m.id,
        })
        with self.assertRaises(Exception):
            self.env['hr.employee.workwear.size'].create({
                'employee_id': self.employee.id, 'type_id': self.type_robe.id, 'size_id': self.size_l.id,
            })

    def test_size_domain_filtered_by_scale(self):
        """п. 10.4/10.10 ТЗ - для типа со шкалой shoe не предлагаются
        буквенные (alpha) размеры."""
        shoe_type = self._make_type('Test Boots', 'shoe')
        shoe_size = self._make_size('Test 42', 'shoe')
        self.assertEqual(shoe_type.size_scale, 'shoe')
        alpha_sizes = self.env['hr.workwear.size'].search([('scale', '=', 'alpha')])
        shoe_sizes = self.env['hr.workwear.size'].search([('scale', '=', 'shoe')])
        self.assertNotIn(self.size_m, shoe_sizes)
        self.assertIn(shoe_size, shoe_sizes)
        self.assertNotIn(shoe_size, alpha_sizes)


@tagged('post_install', '-at_install')
class TestWorkwearOverdue(WorkwearTestMixin, TransactionCase):

    def test_overdue_uses_hire_date_when_no_issue(self):
        """п. 10.6/10.10 ТЗ - без единой выдачи отсчёт идёт от даты приёма."""
        job = self.env['hr.job'].create({'name': 'Test Overdue Job'})
        type_robe = self._make_type('Test Overdue Robe', period=12)
        self.env['hr.workwear.norm'].create({
            'job_id': job.id, 'type_id': type_robe.id, 'quantity': 1, 'period_months': 12,
        })
        employee = self._make_employee(
            'Test Overdue Employee', job_id=job.id, hire_date=date(2020, 1, 1))
        self.assertEqual(employee.workwear_overdue_count, 1)
        self.assertEqual(employee.workwear_next_due_date, date(2021, 1, 1))

    def test_not_required_employee_excluded(self):
        """п. 10.3/10.6 ТЗ - workwear_not_required исключает из просрочки,
        даже если норма формально применима."""
        job = self.env['hr.job'].create({'name': 'Test Excluded Job'})
        type_robe = self._make_type('Test Excluded Robe', period=12)
        self.env['hr.workwear.norm'].create({
            'job_id': job.id, 'type_id': type_robe.id, 'quantity': 1, 'period_months': 12,
        })
        employee = self._make_employee(
            'Test Excluded Employee', job_id=job.id, hire_date=date(2020, 1, 1),
            workwear_not_required=True)
        self.assertEqual(employee.workwear_overdue_count, 0)
        self.assertFalse(employee.workwear_next_due_date)

    def test_norm_requires_job_or_department_not_both(self):
        """п. 10.6/10.10 ТЗ - норма ровно на должность ИЛИ подразделение."""
        job = self.env['hr.job'].create({'name': 'Test Norm Job'})
        department = self.env['hr.department'].create({'name': 'Test Norm Dept'})
        type_robe = self._make_type('Test Norm Robe')
        with self.assertRaises(ValidationError):
            self.env['hr.workwear.norm'].create({
                'job_id': job.id, 'department_id': department.id,
                'type_id': type_robe.id, 'period_months': 12,
            })
        with self.assertRaises(ValidationError):
            self.env['hr.workwear.norm'].create({
                'type_id': type_robe.id, 'period_months': 12,
            })


@tagged('post_install', '-at_install')
class TestWorkwearAccessRights(WorkwearTestMixin, TransactionCase):
    """п. 10.9/10.10 ТЗ - рядовой сотрудник не видит чужие карточки/выдачи
    (ПДн - 64 личных телефона/почты в базе)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group_user = cls.env.ref('base.group_user')
        cls.user_a = cls.env['res.users'].create({
            'name': 'Test Workwear User A', 'login': 'test_workwear_user_a',
            'email': 'test_workwear_user_a@example.com',
            'groups_id': [(6, 0, [group_user.id])],
        })
        cls.user_b = cls.env['res.users'].create({
            'name': 'Test Workwear User B', 'login': 'test_workwear_user_b',
            'email': 'test_workwear_user_b@example.com',
            'groups_id': [(6, 0, [group_user.id])],
        })
        cls.employee_a = cls._make_employee('Test Workwear Employee A', user_id=cls.user_a.id)
        cls.employee_b = cls._make_employee('Test Workwear Employee B', user_id=cls.user_b.id)

    def test_employee_sees_only_own_card(self):
        found = self.env['hr.employee'].with_user(self.user_a).search(
            [('id', 'in', (self.employee_a + self.employee_b).ids)])
        self.assertEqual(found, self.employee_a)
        with self.assertRaises(AccessError):
            self.employee_b.with_user(self.user_a).read(['name'])

    def test_employee_sees_only_own_issues(self):
        issue_a = self.env['hr.workwear.issue'].create({'employee_id': self.employee_a.id})
        issue_b = self.env['hr.workwear.issue'].create({'employee_id': self.employee_b.id})
        found = self.env['hr.workwear.issue'].with_user(self.user_a).search(
            [('id', 'in', (issue_a + issue_b).ids)])
        self.assertEqual(found, issue_a)


@tagged('post_install', '-at_install')
class TestWorkwearPostInitHook(TransactionCase):

    def test_post_init_hook_idempotent(self):
        """п. 10.3/10.10 ТЗ - повторный вызов post_init_hook не плодит
        дубли hr.employee/hr.department."""
        from odoo.addons.hr_workwear import post_init_hook
        employee_count_before = self.env['hr.employee'].search_count([])
        department_count_before = self.env['hr.department'].search_count([])
        post_init_hook(self.env)
        post_init_hook(self.env)
        self.assertEqual(self.env['hr.employee'].search_count([]), employee_count_before)
        self.assertEqual(self.env['hr.department'].search_count([]), department_count_before)


@tagged('post_install', '-at_install')
class TestWorkwearRequirement(WorkwearTestMixin, TransactionCase):

    def test_create_purchase_request_from_requirement(self):
        """п. 10.8/10.10 ТЗ - создаёт корректную текстовую заявку и не
        падает на пустом выборе."""
        empty_result = self.env['hr.workwear.requirement.line'].action_create_purchase_request()
        self.assertFalse(empty_result)

        employee = self._make_employee('Test Requirement Employee')
        type_robe = self._make_type('Test Requirement Robe')
        size_m = self._make_size('Test Requirement M', 'alpha')
        req_line = self.env['hr.workwear.requirement.line'].create({
            'employee_id': employee.id, 'type_id': type_robe.id, 'size_id': size_m.id,
            'quantity': 3, 'due_date': date(2026, 8, 1),
        })
        action = req_line.action_create_purchase_request()
        self.assertEqual(action['res_model'], 'purchase.request')
        request = self.env['purchase.request'].browse(action['res_id'])
        self.assertEqual(len(request.line_ids), 1)
        self.assertEqual(request.line_ids.product_qty, 3)
        self.assertIn(type_robe.name, request.line_ids.name)


@tagged('post_install', '-at_install')
class TestContactKindClassification(WorkwearTestMixin, TransactionCase):
    """Установка hr создаёт res.partner (work_contact_id) на каждого
    hr.employee - штатное поведение Odoo, не отключаем (сломало бы
    переписку/уведомления сотрудника). contact_kind - только
    классификация для фильтров/группировки в "Контакты", чтобы не
    смешивались визуально с поставщиками/клиентами."""

    def test_employee_partner_classified_as_employee_even_if_ranked(self):
        employee = self._make_employee('Test Contact Kind Employee')
        partner = employee.work_contact_id
        self.assertEqual(partner.contact_kind, 'employee')
        # Приоритет "сотрудник" даже если у контакта вдруг проставлен
        # supplier_rank - это в первую очередь рабочий контакт, не карточка
        # поставщика.
        partner.supplier_rank = 1
        partner.invalidate_recordset(['contact_kind'])
        self.assertEqual(partner.contact_kind, 'employee')

    def test_supplier_partner_classified_as_supplier(self):
        partner = self.env['res.partner'].create({'name': 'Test Contact Kind Vendor', 'supplier_rank': 1})
        self.assertEqual(partner.contact_kind, 'supplier')

    def test_plain_contact_classified_as_other(self):
        partner = self.env['res.partner'].create({'name': 'Test Contact Kind Plain'})
        self.assertEqual(partner.contact_kind, 'other')
