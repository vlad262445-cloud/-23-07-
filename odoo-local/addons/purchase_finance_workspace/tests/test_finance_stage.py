from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.purchase_finance_workspace.models.purchase_order import FINANCE_STAGE_PRIORITY


@tagged('post_install', '-at_install')
class TestFinanceStagePriorityOrder(TransactionCase):
    """Порядок из п. 6.1 ТЗ - зафиксирован константой, проверяем сам список,
    а не только поведение через него (защита от случайной перестановки)."""

    def test_priority_order_matches_tz(self):
        self.assertEqual(FINANCE_STAGE_PRIORITY, [
            'inn_mismatch', 'to_pay_urgent', 'to_surcharge', 'to_pay', 'to_upload_slip',
            'done', 'wait_approval',
        ])


@tagged('post_install', '-at_install')
class TestFinanceStage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Finance Vendor', 'vat': '7800000009',
        })
        cls.product = cls.env['product.product'].create({'name': 'Test Finance Product'})
        plan = cls.env['account.analytic.plan'].create({'name': 'Test Finance Plan'})
        cls.analytic_account = cls.env['account.analytic.account'].create({
            'name': 'Test Finance Analytic', 'plan_id': plan.id,
        })
        category_plan = cls.env['account.analytic.plan'].create({'name': 'Test Finance Category Plan'})
        cls.analytic_category = cls.env['account.analytic.account'].create({
            'name': 'Test Finance Category', 'plan_id': category_plan.id,
        })

    def _make_order(self, payment_type, priority='0'):
        order = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'payment_type': payment_type,
            'payment_priority': priority,
            'cost_analytic_account_id': self.analytic_account.id,
            'cost_category_id': self.analytic_category.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'name': self.product.name,
                'product_qty': 1, 'product_uom': self.product.uom_id.id, 'price_unit': 1000.0,
            })],
        })
        request = self.env['purchase.request'].create({
            'purchase_order_id': order.id,
            'line_ids': [(0, 0, {'name': self.product.name, 'product_qty': 1})],
        })
        return order, request

    def _approve(self, order):
        order.action_send_to_approval()
        for line in order.approval_line_ids.filtered(lambda item: item.state == 'pending'):
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

    # --- ветки ------------------------------------------------------------

    def test_wait_approval_before_approval(self):
        order, request = self._make_order('full_prepay')
        self.assertEqual(order.finance_stage, 'wait_approval')

    def test_to_pay_for_prepayment_route(self):
        order, request = self._make_order('full_prepay')
        self._approve(order)
        self.assertEqual(order.finance_stage, 'to_pay')

    def test_to_pay_urgent_with_priority(self):
        order, request = self._make_order('full_prepay', priority='2')
        self._approve(order)
        self.assertEqual(order.finance_stage, 'to_pay_urgent')

    def test_post_payment_before_payment_stays_wait_approval(self):
        # См. NOTES.md - post_payment платит ПОСЛЕ получения, "to_pay" ему
        # не подходит вообще; пока не подошла реальная стадия оплаты,
        # финансисту буквально нечего делать - используем wait_approval,
        # восьмое значение не заводили.
        order, request = self._make_order('post_payment')
        self._approve(order)
        self.assertEqual(order.finance_stage, 'wait_approval')

    def test_to_surcharge(self):
        order, request = self._make_order('split_50_50')
        self._approve(order)
        self._pay(order, request, order.amount_total * 0.4)
        self.assertEqual(order.finance_stage, 'to_surcharge')

    def test_inn_mismatch_outranks_everything_else(self):
        order, request = self._make_order('full_prepay', priority='2')
        self._approve(order)
        # Платёж с несовпадающим ИНН - создаёт payment_line с
        # partner_matched=False, при этом сумма покрывает 100% (что само по
        # себе выглядело бы как "готово"/to_pay_urgent) - inn_mismatch всё
        # равно должен победить по приоритету.
        wizard = self.env['purchase.payment.import.wizard'].create({
            'purchase_order_id': order.id, 'request_id': request.id,
            'recognized_amount': order.amount_total,
            'recognized_recipient_inn': '0000000000', 'partner_expected_inn': self.partner.vat,
            'expected_amount': order.amount_total, 'state': 'recognized',
        })
        wizard.action_confirm()
        order.invalidate_recordset()
        self.assertTrue(order.payment_line_ids.filtered(lambda line: not line.partner_matched))
        self.assertEqual(order.finance_stage, 'inn_mismatch')

    def test_to_upload_slip(self):
        order, request = self._make_order('post_payment')
        self._approve(order)
        # Юнит-проверка самой ветки логики - запрос реально дошёл до вехи
        # invoice_paid (проставляем напрямую, не гоняя весь цикл получения/
        # оплаты), платёжка пропущена вручную.
        request.state = 'invoice_paid'
        order.action_skip_payment()
        order.invalidate_recordset()
        self.assertEqual(order.finance_stage, 'to_upload_slip')

    def test_done_when_fully_paid_and_matched(self):
        order, request = self._make_order('full_prepay')
        self._approve(order)
        self._pay(order, request, order.amount_total)
        self.assertEqual(order.finance_stage, 'done')

    # --- поля-помощники (п. 6.3) --------------------------------------

    def test_amount_residual_purchase(self):
        order, request = self._make_order('full_prepay')
        self._approve(order)
        self._pay(order, request, order.amount_total * 0.3)
        self.assertAlmostEqual(order.amount_residual_purchase, order.amount_total * 0.7, places=2)

    def test_payment_due_date_from_request(self):
        # desired_date - в _LOCKED_FIELDS заявки, как только у неё есть
        # purchase_order_id (см. purchase_request.py write()) - значение
        # нужно задать в самом create(), а не отдельным write() после.
        order = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'payment_type': 'full_prepay',
            'cost_analytic_account_id': self.analytic_account.id,
            'cost_category_id': self.analytic_category.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'name': self.product.name,
                'product_qty': 1, 'product_uom': self.product.uom_id.id, 'price_unit': 1000.0,
            })],
        })
        self.env['purchase.request'].create({
            'purchase_order_id': order.id,
            'desired_date': '2026-09-01',
            'line_ids': [(0, 0, {'name': self.product.name, 'product_qty': 1})],
        })
        order.invalidate_recordset()
        self.assertEqual(str(order.payment_due_date), '2026-09-01')

    def test_approval_date_set_once_and_not_reset(self):
        order, request = self._make_order('full_prepay')
        self.assertFalse(order.approval_date)
        self._approve(order)
        first_date = order.approval_date
        self.assertTrue(first_date)

        order._compute_finance_stage()
        self.assertEqual(order.approval_date, first_date)

    def test_days_waiting_payment(self):
        order, request = self._make_order('full_prepay')
        self._approve(order)
        order.approval_date = fields.Datetime.now() - timedelta(days=3)
        order._compute_days_waiting_payment()
        self.assertEqual(order.days_waiting_payment, 3)

    def test_finance_partner_display_name_falls_back_to_full_name(self):
        # purchase_registry_ux (короткое имя) может быть не установлен -
        # без него finance_partner_display_name обязан отдать хотя бы
        # полное имя, а не упасть.
        order, request = self._make_order('full_prepay')
        self.assertEqual(order.finance_partner_display_name, self.partner.name)
