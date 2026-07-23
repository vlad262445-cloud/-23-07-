from odoo.tests.common import TransactionCase, tagged


class VendorCardTestMixin:
    """Тот же стиль _make_order/_approve/_pay/_receive, что уже используется
    в purchase_registry_ux и purchase_finance_workspace/dashboard."""

    def _make_partner_and_product(self, suffix):
        partner = self.env['res.partner'].create({
            'name': f'Test Vendor Card {suffix}', 'supplier_rank': 1, 'vat': f'78000002{suffix}',
        })
        product = self.env['product.product'].create({'name': f'Test Vendor Card Product {suffix}'})
        return partner, product

    def _make_order(self, partner, product, payment_type='post_payment', amount=1000.0):
        order = self.env['purchase.order'].create({
            'partner_id': partner.id,
            'payment_type': payment_type,
            'cost_analytic_account_id': self.analytic_account.id,
            'cost_category_id': self.category.id,
            'order_line': [(0, 0, {
                'product_id': product.id, 'name': product.name,
                'product_qty': 1, 'product_uom': product.uom_id.id, 'price_unit': amount,
            })],
        })
        request = self.env['purchase.request'].create({
            'purchase_order_id': order.id,
            'line_ids': [(0, 0, {'name': product.name, 'product_qty': 1})],
        })
        return order, request

    def _approve(self, order):
        order.action_send_to_approval()
        for line in order.approval_line_ids.filtered(lambda l: l.state == 'pending'):
            line.with_user(line.approver_id).action_approve()
        order.invalidate_recordset()

    def _pay(self, order, request, partner, amount):
        wizard = self.env['purchase.payment.import.wizard'].create({
            'purchase_order_id': order.id, 'request_id': request.id,
            'recognized_amount': amount,
            'recognized_recipient_inn': partner.vat, 'partner_expected_inn': partner.vat,
            'expected_amount': order.amount_total, 'state': 'recognized',
        })
        wizard.action_confirm()
        order.invalidate_recordset()

    def _receive(self, order):
        order.action_arrange_delivery()
        picking = self.env['stock.picking'].search([('group_id', '=', order.group_id.id)])
        picking.button_validate()
        order.invalidate_recordset()


@tagged('post_install', '-at_install')
class TestVendorStats(VendorCardTestMixin, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plan = cls.env['account.analytic.plan'].create({'name': 'Test Vendor Card Plan'})
        cls.analytic_account = cls.env['account.analytic.account'].create(
            {'name': 'Test Vendor Card Analytic', 'plan_id': plan.id})
        category_plan = cls.env['account.analytic.plan'].create({'name': 'Test Vendor Card Category Plan'})
        cls.category = cls.env['account.analytic.account'].create(
            {'name': 'Test Vendor Card Category', 'plan_id': category_plan.id})

    def test_stats_match_finance_report_module6(self):
        """Агрегаты по поставщику совпадают с суммами из purchase.finance.
        report модуля 6 на одном и том же наборе данных (п. 8.8 ТЗ) -
        защита от расхождения формул между дашбордом и карточкой."""
        partner, product = self._make_partner_and_product('1')
        order1, request1 = self._make_order(partner, product, 'full_prepay', 500.0)
        self._approve(order1)
        self._pay(order1, request1, partner, order1.amount_total)
        order2, _r2 = self._make_order(partner, product, 'post_payment', 300.0)
        self._approve(order2)

        report_rows = self.env['purchase.finance.report'].search([('partner_id', '=', partner.id)])
        report_total = sum(report_rows.mapped('amount_total'))
        self.assertAlmostEqual(partner.vendor_invoiced_total, report_total, places=2)
        self.assertEqual(partner.vendor_order_count, 2)

    def test_invoiced_equals_paid_plus_residual(self):
        partner, product = self._make_partner_and_product('2')
        order, request = self._make_order(partner, product, 'full_prepay', 1000.0)
        self._approve(order)
        self._pay(order, request, partner, 400.0)
        self.assertAlmostEqual(
            partner.vendor_invoiced_total,
            partner.vendor_paid_total + partner.vendor_residual_total, places=2)

    def test_frozen_and_debt_mutually_exclusive(self):
        """vendor_frozen_total и vendor_debt_total взаимоисключающи для
        одного заказа (п. 8.8 ТЗ): предоплата без приёмки - в frozen, не в
        debt; приёмка без оплаты - в debt, не в frozen."""
        partner, product = self._make_partner_and_product('3')
        order_prepaid, request_prepaid = self._make_order(partner, product, 'full_prepay', 500.0)
        self._approve(order_prepaid)
        self._pay(order_prepaid, request_prepaid, partner, order_prepaid.amount_total)

        order_received, _r = self._make_order(partner, product, 'post_payment', 700.0)
        self._approve(order_received)
        self._receive(order_received)

        partner.invalidate_recordset()
        # Сравниваем с order.amount_total, а не с "сырыми" price_unit - на
        # продукте может быть настроен налог поставщика по умолчанию,
        # amount_total его уже учитывает (тот же нюанс, что и в
        # purchase_finance_dashboard/tests).
        self.assertAlmostEqual(partner.vendor_frozen_total, order_prepaid.amount_total, places=2)
        self.assertAlmostEqual(partner.vendor_debt_total, order_received.amount_total, places=2)

    def test_received_uses_stock_picking_not_request_state_regression(self):
        """Проверка "получено" опирается на stock.picking, а не на
        request_state - регрессионный тест на маршруте post_payment, где
        после оплаты request_state уходит в invoice_paid (п. 8.8 ТЗ, тот же
        класс бага, что уже описан в purchase_finance_dashboard)."""
        partner, product = self._make_partner_and_product('4')
        order, request = self._make_order(partner, product, 'post_payment', 900.0)
        self._approve(order)
        self._receive(order)
        self._pay(order, request, partner, order.amount_total)
        self.assertEqual(order.request_state, 'invoice_paid')
        partner.invalidate_recordset()
        # Оплачено и получено полностью - ни в заморозке, ни в долге.
        self.assertEqual(partner.vendor_frozen_total, 0.0)
        self.assertEqual(partner.vendor_debt_total, 0.0)

    def test_supplier_without_orders_returns_zeros(self):
        partner = self.env['res.partner'].create({'name': 'Test Vendor Card No Orders', 'supplier_rank': 1})
        self.assertEqual(partner.vendor_invoiced_total, 0.0)
        self.assertEqual(partner.vendor_order_count, 0)
        self.assertEqual(partner.vendor_avg_order, 0.0)
        self.assertFalse(partner.vendor_last_order_date)
        self.assertEqual(partner.vendor_debt_total, 0.0)
        self.assertEqual(partner.vendor_frozen_total, 0.0)

    def test_query_count_does_not_scale_with_partner_count(self):
        """"Один read_group на весь recordset, не по одному на партнёра"
        (п. 8.3/8.8 ТЗ) - проверяем, что число запросов не растёт
        пропорционально числу поставщиков (5 vs 20+), а не жёстко
        фиксированное число (устойчивее к мелким изменениям реализации)."""
        def _make_n_suppliers_with_orders(n, suffix):
            partners = self.env['res.partner']
            for i in range(n):
                partner, product = self._make_partner_and_product(f'{suffix}{i}')
                self._make_order(partner, product, 'post_payment', 100.0 + i)
                partners |= partner
            # Досчитать и сохранить ВСЕ хранимые поля (vendor_data_quality/
            # vendor_duplicate_group_key/...) ДО замера - иначе в счётчик
            # запросов попадёт их пересчёт (он не обязан быть O(1) - TZ
            # п. 8.3 требует это только от финансовых полей "на лету"), а
            # не только вызванного ниже vendor_invoiced_total.
            self.env.flush_all()
            partners.invalidate_recordset()
            return partners

        small = _make_n_suppliers_with_orders(5, 'q5_')
        queries_small = self._count_queries(lambda: small.mapped('vendor_invoiced_total'))

        big = _make_n_suppliers_with_orders(25, 'q25_')
        queries_big = self._count_queries(lambda: big.mapped('vendor_invoiced_total'))

        # Не строгое равенство (сама Odoo может добавить кэш-запросы по
        # доступу), но рост точно не должен быть пропорционален разнице в
        # 20 партнёров - иначе это ровно тот баг ("запрос на каждого
        # партнёра"), которого требовалось избежать.
        self.assertLess(
            queries_big - queries_small, 5,
            f'запросов на 5 партнёров: {queries_small}, на 25: {queries_big} - '
            f'похоже на запрос-на-партнёра, а не на агрегат по всему набору')

    def _count_queries(self, func):
        count = [0]
        original_execute = self.env.cr.execute

        def _patched(*args, **kwargs):
            count[0] += 1
            return original_execute(*args, **kwargs)

        self.env.cr.execute = _patched
        try:
            func()
        finally:
            self.env.cr.execute = original_execute
        return count[0]


@tagged('post_install', '-at_install')
class TestVendorDuplicates(TransactionCase):
    """п. 8.6/8.8 ТЗ - поиск дублей по нормализованному ИНН/названию."""

    def test_duplicate_by_name_without_vat(self):
        p1 = self.env['res.partner'].create({'name': 'ООО "Дата-В"', 'supplier_rank': 1})
        p2 = self.env['res.partner'].create({'name': 'ООО Дата-В', 'supplier_rank': 1})
        p3 = self.env['res.partner'].create({'name': 'Совершенно другой поставщик', 'supplier_rank': 1})
        self.assertEqual(p1.vendor_duplicate_group_key, p2.vendor_duplicate_group_key)
        self.assertTrue(p1.vendor_is_possible_duplicate)
        self.assertTrue(p2.vendor_is_possible_duplicate)
        self.assertFalse(p3.vendor_is_possible_duplicate)

    def test_duplicate_by_matching_vat_outranks_name(self):
        p1 = self.env['res.partner'].create(
            {'name': 'Первое название', 'supplier_rank': 1, 'vat': '7800000030'})
        p2 = self.env['res.partner'].create(
            {'name': 'Совсем другое название', 'supplier_rank': 1, 'vat': '7800000030'})
        self.assertEqual(p1.vendor_duplicate_group_key, p2.vendor_duplicate_group_key)
        self.assertTrue(p1.vendor_is_possible_duplicate)

    def test_action_open_vendor_duplicates_does_not_crash_and_forces_recompute(self):
        p1 = self.env['res.partner'].create({'name': 'ООО "Форс Рекомпьют"', 'supplier_rank': 1})
        action = self.env['res.partner'].action_open_vendor_duplicates()
        self.assertEqual(action['res_model'], 'res.partner')
        self.assertNotIn(p1.id, action['domain'][0][2])

    def test_non_supplier_has_no_duplicate_key(self):
        contact = self.env['res.partner'].create({'name': 'Обычный контакт, не поставщик'})
        self.assertFalse(contact.vendor_duplicate_group_key)
        self.assertFalse(contact.vendor_is_possible_duplicate)
