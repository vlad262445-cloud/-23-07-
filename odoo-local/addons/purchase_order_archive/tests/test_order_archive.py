from datetime import datetime, timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.purchase_order_archive.models.purchase_order import _add_working_days_fallback


@tagged('post_install', '-at_install')
class TestWorkingDaysFallback(TransactionCase):
    """Функция не зависит от ORM - чистая проверка арифметики дат, без
    setUp'а календаря компании."""

    def test_skips_weekend(self):
        # 2026-07-20 - понедельник. +5 рабочих дней: вт/ср/чт/пт (4), затем
        # суббота/воскресенье не считаются, 5-й рабочий день - понедельник
        # 2026-07-27.
        start = datetime(2026, 7, 20)
        result = _add_working_days_fallback(start, 5)
        self.assertEqual(result, datetime(2026, 7, 27))
        self.assertEqual(result.weekday(), 0, 'должен получиться понедельник')

    def test_short_span_without_weekend(self):
        start = datetime(2026, 7, 20)  # понедельник
        result = _add_working_days_fallback(start, 2)
        self.assertEqual(result, datetime(2026, 7, 22))  # среда, без выходных на пути


@tagged('post_install', '-at_install')
class TestOrderArchive(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Archive Vendor', 'vat': '7800000004',
        })
        cls.product = cls.env['product.product'].create({'name': 'Test Archive Product'})
        plan = cls.env['account.analytic.plan'].create({'name': 'Test Archive Plan'})
        cls.analytic_account = cls.env['account.analytic.account'].create({
            'name': 'Test Archive Analytic', 'plan_id': plan.id,
        })
        category_plan = cls.env['account.analytic.plan'].create({'name': 'Test Archive Category Plan'})
        cls.analytic_category = cls.env['account.analytic.account'].create({
            'name': 'Test Archive Category', 'plan_id': category_plan.id,
        })

    def _make_order(self, price_unit=100.0):
        order = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'payment_type': 'post_payment',
            'cost_analytic_account_id': self.analytic_account.id,
            'cost_category_id': self.analytic_category.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'name': self.product.name,
                'product_qty': 1,
                'product_uom': self.product.uom_id.id,
                'price_unit': price_unit,
            })],
        })
        request = self.env['purchase.request'].create({
            'purchase_order_id': order.id,
            'line_ids': [(0, 0, {'name': self.product.name, 'product_qty': 1})],
        })
        order.action_send_to_approval()
        for line in order.approval_line_ids.filtered(lambda item: item.state == 'pending'):
            line.with_user(line.approver_id).action_approve()
        order.invalidate_recordset()
        request.invalidate_recordset()
        return order, request

    def _receive_goods(self, order):
        order.action_arrange_delivery()
        picking = self.env['stock.picking'].search([('group_id', '=', order.group_id.id)])
        picking.button_validate()

    def _pay(self, order, request, amount):
        wizard = self.env['purchase.payment.import.wizard'].create({
            'purchase_order_id': order.id,
            'request_id': request.id,
            'recognized_amount': amount,
            'recognized_recipient_inn': self.partner.vat,
            'partner_expected_inn': self.partner.vat,
            'expected_amount': order.amount_total,
            'state': 'recognized',
        })
        wizard.action_confirm()

    def _complete_order(self):
        """Полный цикл: получено, УПД реально прикреплён (а не пропущен -
        'пропущен' по-прежнему считается document_status='blocked' в базовом
        модуле, см. _compute_document_status: обе ветки updd_skipped
        добавляют пункт в missing, разнится только формулировка), оплачено
        полностью."""
        order, request = self._make_order()
        self._receive_goods(order)
        self.env['purchase.updd.line'].create({
            'purchase_order_id': order.id,
            'seller_inn': self.partner.vat,
            'partner_matched': True,
        })
        self._pay(order, request, order.amount_total)
        order.invalidate_recordset()
        return order, request

    def test_incomplete_payment_does_not_complete_order(self):
        order, request = self._make_order()
        self._receive_goods(order)
        order.action_skip_updd()
        self._pay(order, request, order.amount_total * 0.5)
        order.invalidate_recordset()
        self.assertFalse(order.is_completed, 'при неполной оплате заказ не должен считаться завершённым')
        self.assertFalse(order.completed_date)

    def test_order_completes_when_all_conditions_met(self):
        order, request = self._complete_order()
        self.assertTrue(order.is_completed)
        self.assertTrue(order.completed_date)

    def test_completed_date_not_overwritten_on_recompute(self):
        order, request = self._complete_order()
        first_date = order.completed_date
        self.assertTrue(first_date)

        # Повторный пересчёт (например, из-за пересчёта другого зависимого
        # поля) не должен переставить дату завершения.
        order._compute_is_completed()
        self.assertEqual(order.completed_date, first_date)

    def test_cron_does_not_archive_before_deadline(self):
        order, request = self._complete_order()
        # completed_date только что выставлен - 5 рабочих дней точно не прошло.
        self.env['purchase.order']._cron_archive_completed_orders()
        order.invalidate_recordset()
        self.assertFalse(order.is_archived, 'не должно архивироваться раньше срока')

    def test_cron_archives_after_deadline(self):
        order, request = self._complete_order()
        # 10 календарных дней назад - заведомо больше 5 рабочих дней при
        # любом расположении выходных.
        order.completed_date = fields.Datetime.now() - timedelta(days=10)
        self.env['purchase.order']._cron_archive_completed_orders()
        order.invalidate_recordset()
        self.assertTrue(order.is_archived)
        self.assertTrue(order.archived_date)
        self.assertTrue(
            any('автоматически перемещена в архив' in body for body in order.message_ids.mapped('body')))

    def test_manual_archive_and_unarchive(self):
        order, request = self._complete_order()
        order.action_archive_manually()
        self.assertTrue(order.is_archived)
        self.assertTrue(order.archived_date)

        order.action_unarchive()
        self.assertFalse(order.is_archived)
        self.assertTrue(
            any('возвращена из архива' in body for body in order.message_ids.mapped('body')))

    def test_archived_order_hidden_from_registry_domain_visible_in_archive(self):
        order, request = self._complete_order()
        order.action_archive_manually()

        not_archived = self.env['purchase.order'].search([('is_archived', '=', False)])
        archived = self.env['purchase.order'].search([('is_archived', '=', True)])
        self.assertNotIn(order, not_archived)
        self.assertIn(order, archived)

        # Доступ по прямой ссылке (обычное чтение по id) не завязан на
        # is_archived - в отличие от родного active=, свой флаг не включает
        # никакой ORM-фильтрации.
        self.assertEqual(self.env['purchase.order'].browse(order.id).name, order.name)

    def test_archiving_does_not_break_request_and_picking_links(self):
        order, request = self._complete_order()
        picking = self.env['stock.picking'].search([('group_id', '=', order.group_id.id)])
        self.assertTrue(picking)

        order.action_archive_manually()

        self.assertEqual(request.purchase_order_id, order)
        self.assertIn(request, order.request_ids)
        self.assertEqual(
            self.env['stock.picking'].search([('group_id', '=', order.group_id.id)]), picking)
