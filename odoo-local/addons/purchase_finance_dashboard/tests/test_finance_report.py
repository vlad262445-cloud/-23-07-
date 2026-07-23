from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


class FinanceReportTestMixin:
    """Общие фикстуры/хелперы для тестов purchase.finance.report - тот же
    стиль _make_order/_approve/_pay/_receive, что уже используется в
    purchase_registry_ux и purchase_finance_workspace."""

    @classmethod
    def _setup_vendor_and_product(cls, suffix):
        cls.partner = cls.env['res.partner'].create({
            'name': f'Test FD Vendor {suffix}', 'vat': f'78000001{suffix}',
        })
        cls.product = cls.env['product.product'].create({'name': f'Test FD Product {suffix}'})

    def _make_order(self, analytic_account, category, payment_type='post_payment', amount=1000.0):
        order = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'payment_type': payment_type,
            'cost_analytic_account_id': analytic_account.id if analytic_account else False,
            'cost_category_id': category.id if category else False,
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'name': self.product.name,
                'product_qty': 1, 'product_uom': self.product.uom_id.id, 'price_unit': amount,
            })],
        })
        request = self.env['purchase.request'].create({
            'purchase_order_id': order.id,
            'line_ids': [(0, 0, {'name': self.product.name, 'product_qty': 1})],
        })
        return order, request

    def _approve(self, order):
        order.action_send_to_approval()
        for line in order.approval_line_ids.filtered(lambda l: l.state == 'pending'):
            line.with_user(line.approver_id).action_approve()
        order.invalidate_recordset()

    def _pay(self, order, request, amount):
        wizard = self.env['purchase.payment.import.wizard'].create({
            'purchase_order_id': order.id, 'request_id': request.id,
            'recognized_amount': amount,
            'recognized_recipient_inn': self.partner.vat, 'partner_expected_inn': self.partner.vat,
            'expected_amount': order.amount_total, 'state': 'recognized',
        })
        wizard.action_confirm()
        order.invalidate_recordset()

    def _receive(self, order):
        order.action_arrange_delivery()
        picking = self.env['stock.picking'].search([('group_id', '=', order.group_id.id)])
        picking.button_validate()
        order.invalidate_recordset()

    def _report_line(self, order):
        self.env.flush_all()
        return self.env['purchase.finance.report'].search([('order_id', '=', order.id)])


@tagged('post_install', '-at_install')
class TestFinanceReportAmounts(FinanceReportTestMixin, TransactionCase):
    """п. 7.9 ТЗ: предоплата без приёмки -> amount_frozen, не
    amount_debt_received; приёмка без оплаты -> наоборот; оплачено и
    получено -> ни туда, ни туда. is_received - через stock.picking, а не
    request_state (регрессия на маршруте post_payment, где после оплаты
    request_state уходит в invoice_paid)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_vendor_and_product('1')
        plan = cls.env['account.analytic.plan'].create({'name': 'Test FD Plan 1'})
        cls.analytic_account = cls.env['account.analytic.account'].create(
            {'name': 'Test FD Analytic 1', 'plan_id': plan.id})
        category_plan = cls.env['account.analytic.plan'].create({'name': 'Test FD Category Plan 1'})
        cls.category = cls.env['account.analytic.account'].create(
            {'name': 'Test FD Category 1', 'plan_id': category_plan.id})

    def test_prepay_without_receipt_is_frozen_not_debt(self):
        order, request = self._make_order(self.analytic_account, self.category, 'full_prepay')
        self._approve(order)
        self._pay(order, request, order.amount_total)
        line = self._report_line(order)
        self.assertFalse(line.is_received)
        self.assertAlmostEqual(line.amount_frozen, order.amount_total, places=2)
        self.assertEqual(line.amount_debt_received, 0.0)

    def test_receipt_without_payment_is_debt_not_frozen(self):
        order, request = self._make_order(self.analytic_account, self.category, 'post_payment')
        self._approve(order)
        self._receive(order)
        line = self._report_line(order)
        self.assertTrue(line.is_received)
        self.assertAlmostEqual(line.amount_debt_received, order.amount_total, places=2)
        self.assertEqual(line.amount_frozen, 0.0)

    def test_paid_and_received_is_neither_frozen_nor_debt(self):
        order, request = self._make_order(self.analytic_account, self.category, 'post_payment')
        self._approve(order)
        self._receive(order)
        self._pay(order, request, order.amount_total)
        line = self._report_line(order)
        self.assertTrue(line.is_received)
        self.assertEqual(line.amount_frozen, 0.0)
        self.assertEqual(line.amount_debt_received, 0.0)

    def test_is_received_uses_stock_picking_not_request_state_regression(self):
        order, request = self._make_order(self.analytic_account, self.category, 'post_payment')
        self._approve(order)
        self._receive(order)
        self._pay(order, request, order.amount_total)
        self.assertEqual(order.request_state, 'invoice_paid')
        line = self._report_line(order)
        self.assertTrue(line.is_received)

    def test_order_without_category_in_no_analytics_and_pivot_safe(self):
        order, _request = self._make_order(self.analytic_account, None, 'post_payment', amount=50.0)
        line = self._report_line(order)
        self.assertFalse(line.cost_category_id)
        grouped = self.env['purchase.finance.report'].read_group(
            [('order_id', '=', order.id)], ['amount_total:sum'], ['cost_category_id'])
        self.assertEqual(len(grouped), 1)
        self.assertFalse(grouped[0]['cost_category_id'])


@tagged('post_install', '-at_install')
class TestCostPlanRootId(FinanceReportTestMixin, TransactionCase):
    """cost_plan_root_id должен возвращать корневой план для статьи любого
    уровня вложенности (п. 7.9 ТЗ) - используется родной root_plan_id
    account.analytic.account (проверено на живых данных: подтверждено, что
    Odoo уже считает его сам, рекурсивный CTE не понадобился - см. NOTES.md),
    и суммы по всем cost_plan_root_id не теряют и не задваивают ни один
    заказ при подъёме по иерархии."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_vendor_and_product('2')
        cls.root_plan = cls.env['account.analytic.plan'].create({'name': 'Test FD Root Plan'})
        cls.sub_plan = cls.env['account.analytic.plan'].create(
            {'name': 'Test FD Sub Plan', 'parent_id': cls.root_plan.id})
        cls.nested_account = cls.env['account.analytic.account'].create(
            {'name': 'Test FD Nested Account', 'code': '1.121x', 'plan_id': cls.sub_plan.id})
        cls.flat_account = cls.env['account.analytic.account'].create(
            {'name': 'Test FD Flat Account', 'code': '1.20x', 'plan_id': cls.root_plan.id})
        cls.invest_plan = cls.env['account.analytic.plan'].create({'name': 'Test FD Invest Plan'})
        cls.invest_account = cls.env['account.analytic.account'].create(
            {'name': 'Test FD Invest Account', 'code': '3.10x', 'plan_id': cls.invest_plan.id})
        category_plan = cls.env['account.analytic.plan'].create({'name': 'Test FD Category Plan 2'})
        cls.category = cls.env['account.analytic.account'].create(
            {'name': 'Test FD Category 2', 'plan_id': category_plan.id})

    def test_root_plan_resolves_regardless_of_nesting_depth(self):
        self.assertEqual(self.nested_account.root_plan_id, self.root_plan)
        self.assertEqual(self.flat_account.root_plan_id, self.root_plan)
        self.assertEqual(self.invest_account.root_plan_id, self.invest_plan)

        order_nested, _r1 = self._make_order(self.nested_account, self.category, amount=100.0)
        order_flat, _r2 = self._make_order(self.flat_account, self.category, amount=200.0)
        order_invest, _r3 = self._make_order(self.invest_account, self.category, amount=300.0)
        self.env.flush_all()
        report = self.env['purchase.finance.report']
        self.assertEqual(
            report.search([('order_id', '=', order_nested.id)]).cost_plan_root_id, self.root_plan)
        self.assertEqual(
            report.search([('order_id', '=', order_flat.id)]).cost_plan_root_id, self.root_plan)
        self.assertEqual(
            report.search([('order_id', '=', order_invest.id)]).cost_plan_root_id, self.invest_plan)

    def test_sum_by_root_plan_equals_total_no_double_counting(self):
        order_a, _r1 = self._make_order(self.nested_account, self.category, amount=111.0)
        order_b, _r2 = self._make_order(self.flat_account, self.category, amount=222.0)
        order_c, _r3 = self._make_order(self.invest_account, self.category, amount=333.0)
        self.env.flush_all()
        lines = self.env['purchase.finance.report'].search(
            [('order_id', 'in', (order_a | order_b | order_c).ids)])
        total = sum(lines.mapped('amount_total'))
        by_root = {}
        for line in lines:
            by_root[line.cost_plan_root_id] = by_root.get(line.cost_plan_root_id, 0.0) + line.amount_total
        # Сравниваем с order.amount_total, а не с "сырыми" price_unit -
        # на продукте может быть настроен налог поставщика по умолчанию,
        # amount_total его уже учитывает.
        self.assertAlmostEqual(sum(by_root.values()), total, places=2)
        self.assertAlmostEqual(
            by_root[self.root_plan], order_a.amount_total + order_b.amount_total, places=2)
        self.assertAlmostEqual(by_root[self.invest_plan], order_c.amount_total, places=2)


@tagged('post_install', '-at_install')
class TestEmptyReportResultSet(TransactionCase):
    """Дашборд не должен падать на пустом результате (п. 7.9 ТЗ) - без
    удаления реальных заказов восстановленной базы (риск каскадных
    побочных эффектов ради теста, который и так проверяется пустым
    доменом): ищем заведомо несуществующий order_id."""

    def test_search_and_read_group_with_no_matches(self):
        report = self.env['purchase.finance.report']
        empty = report.search([('order_id', '=', 0)])
        self.assertEqual(len(empty), 0)
        grouped = report.read_group(
            [('order_id', '=', 0)], ['amount_total:sum'], ['cost_plan_root_id'])
        self.assertEqual(grouped, [])


@tagged('post_install', '-at_install')
class TestDaysInStage(FinanceReportTestMixin, TransactionCase):
    """lifecycle_stage_since/days_in_stage - мягкая зависимость от
    purchase_registry_ux (см. models/purchase_order.py)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_vendor_and_product('3')
        plan = cls.env['account.analytic.plan'].create({'name': 'Test FD Plan 3'})
        cls.analytic_account = cls.env['account.analytic.account'].create(
            {'name': 'Test FD Analytic 3', 'plan_id': plan.id})
        category_plan = cls.env['account.analytic.plan'].create({'name': 'Test FD Category Plan 3'})
        cls.category = cls.env['account.analytic.account'].create(
            {'name': 'Test FD Category 3', 'plan_id': category_plan.id})

    def test_lifecycle_stage_since_resets_only_on_real_transition(self):
        if 'lifecycle_stage' not in self.env['purchase.order']._fields:
            self.skipTest('purchase_registry_ux не установлен - lifecycle_stage_since не имеет смысла')
        order, _request = self._make_order(self.analytic_account, self.category, 'full_prepay')
        first_marker = order.lifecycle_stage_marker
        first_since = order.lifecycle_stage_since
        self.assertTrue(first_since)

        # Пересчёт без реальной смены этапа не должен трогать метку.
        order._compute_lifecycle_stage_since()
        self.assertEqual(order.lifecycle_stage_since, first_since)

        self._approve(order)
        order.invalidate_recordset()
        self.assertNotEqual(order.lifecycle_stage_marker, first_marker)
        self.assertGreaterEqual(order.lifecycle_stage_since, first_since)


@tagged('post_install', '-at_install')
class TestDashboardAccess(TransactionCase):
    """group_ceo/group_owner видят отчёт, но не получают прав на запись в
    purchase.order; пользователь только с group_chief_buyer открывает все
    четыре дашборда без ошибки доступа и без единого нового права на
    запись (п. 7.9 ТЗ)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base_user = cls.env.ref('base.group_user')
        cls.ceo_group = cls.env.ref('purchase_finance_dashboard.group_ceo')
        cls.owner_group = cls.env.ref('purchase_finance_dashboard.group_owner')
        cls.chief_buyer_group = cls.env.ref('purchase_pdf_import.group_chief_buyer')
        cls.ceo_user = cls.env['res.users'].create({
            'name': 'Test FD CEO', 'login': 'test_fd_ceo@example.com',
            'groups_id': [(6, 0, [base_user.id, cls.ceo_group.id])],
        })
        cls.owner_user = cls.env['res.users'].create({
            'name': 'Test FD Owner', 'login': 'test_fd_owner@example.com',
            'groups_id': [(6, 0, [base_user.id, cls.owner_group.id])],
        })
        cls.chief_buyer_user = cls.env['res.users'].create({
            'name': 'Test FD Chief Buyer', 'login': 'test_fd_chief_buyer@example.com',
            'groups_id': [(6, 0, [base_user.id, cls.chief_buyer_group.id])],
        })

    def test_ceo_and_owner_read_report_but_cannot_write_order(self):
        partner = self.env['res.partner'].create({'name': 'Test FD Access Vendor'})
        order = self.env['purchase.order'].create({'partner_id': partner.id})
        for user in (self.ceo_user, self.owner_user):
            self.env['purchase.finance.report'].with_user(user).search([], limit=1)
            with self.assertRaises(AccessError):
                order.with_user(user).write({'partner_ref': 'hack'})

    def test_chief_buyer_only_opens_all_four_dashboards_read_only(self):
        user = self.chief_buyer_user
        for model_name in (
                'purchase.finance.report', 'purchase.approval.line',
                'vendor.delay.report', 'purchase.order'):
            self.env[model_name].with_user(user).search([], limit=1)
        report_access = self.env['ir.model.access'].search([
            ('model_id.model', '=', 'purchase.finance.report'),
            ('group_id', '=', self.chief_buyer_group.id),
        ])
        self.assertTrue(report_access)
        self.assertTrue(all(
            not access.perm_write and not access.perm_create and not access.perm_unlink
            for access in report_access))
