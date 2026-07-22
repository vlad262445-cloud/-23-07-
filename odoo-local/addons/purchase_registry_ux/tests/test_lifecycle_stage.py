from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLifecycleStage(TransactionCase):
    """lifecycle_progress должен монотонно расти по ходу процесса для всех
    трёх payment_type (п. 2.4 ТЗ). 'completed'/100 проверяется отдельно и
    только если на модели физически есть is_completed (purchase_order_archive
    может быть не установлен - см. NOTES.md, это модуль намеренно не требует
    его в манифесте)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Lifecycle Vendor', 'vat': '7800000006',
        })
        cls.product = cls.env['product.product'].create({'name': 'Test Lifecycle Product'})
        plan = cls.env['account.analytic.plan'].create({'name': 'Test Lifecycle Plan'})
        cls.analytic_account = cls.env['account.analytic.account'].create({
            'name': 'Test Lifecycle Analytic', 'plan_id': plan.id,
        })
        category_plan = cls.env['account.analytic.plan'].create({'name': 'Test Lifecycle Category Plan'})
        cls.analytic_category = cls.env['account.analytic.account'].create({
            'name': 'Test Lifecycle Category', 'plan_id': category_plan.id,
        })

    def _make_order(self, payment_type):
        order = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'payment_type': payment_type,
            'cost_analytic_account_id': self.analytic_account.id,
            'cost_category_id': self.analytic_category.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'name': self.product.name,
                'product_qty': 1, 'product_uom': self.product.uom_id.id, 'price_unit': 100.0,
            })],
        })
        request = self.env['purchase.request'].create({
            'purchase_order_id': order.id,
            'line_ids': [(0, 0, {'name': self.product.name, 'product_qty': 1})],
        })
        return order, request

    def _send_and_approve(self, order):
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

    def _receive(self, order):
        order.action_arrange_delivery()
        picking = self.env['stock.picking'].search([('group_id', '=', order.group_id.id)])
        picking.button_validate()
        order.invalidate_recordset()

    def _assert_monotonic(self, sequence):
        for earlier, later in zip(sequence, sequence[1:]):
            self.assertLessEqual(earlier, later, f'прогресс не должен уменьшаться: {sequence}')

    def _maybe_check_completed(self, order, request):
        if 'is_completed' not in order._fields:
            return
        self.env['purchase.updd.line'].create({
            'purchase_order_id': order.id, 'seller_inn': self.partner.vat, 'partner_matched': True,
        })
        remaining = order.amount_total - order.amount_paid
        if remaining > 0:
            self._pay(order, request, remaining)
        order.invalidate_recordset()
        self.assertTrue(order.is_completed)
        self.assertEqual(order.lifecycle_stage, 'completed')
        self.assertEqual(order.lifecycle_progress, 100)

    def test_draft_stage_before_sending(self):
        order, request = self._make_order('post_payment')
        self.assertEqual(order.lifecycle_stage, 'draft')
        self.assertEqual(order.lifecycle_progress, 10)

    def test_to_approve_stage(self):
        order, request = self._make_order('post_payment')
        order.action_send_to_approval()
        order.invalidate_recordset()
        self.assertEqual(order.lifecycle_stage, 'to_approve')
        self.assertEqual(order.lifecycle_progress, 25)

    def test_declined_and_cancel_are_zero(self):
        order, request = self._make_order('post_payment')
        order.action_send_to_approval()
        order.approval_state = 'declined'
        self.assertEqual(order.lifecycle_stage, 'declined')
        self.assertEqual(order.lifecycle_progress, 0)

        order2, request2 = self._make_order('post_payment')
        order2.state = 'cancel'
        self.assertEqual(order2.lifecycle_stage, 'cancel')
        self.assertEqual(order2.lifecycle_progress, 0)

    def test_full_prepay_monotonic(self):
        order, request = self._make_order('full_prepay')
        progress = [order.lifecycle_progress]
        self._send_and_approve(order)
        progress.append(order.lifecycle_progress)
        self.assertEqual(order.lifecycle_stage, 'approved')

        self._pay(order, request, order.amount_total)
        progress.append(order.lifecycle_progress)
        self.assertEqual(order.lifecycle_stage, 'prepaid')

        self._receive(order)
        progress.append(order.lifecycle_progress)
        self.assertEqual(order.lifecycle_stage, 'in_stock')

        self._assert_monotonic(progress)
        self._maybe_check_completed(order, request)

    def test_split_50_50_monotonic(self):
        order, request = self._make_order('split_50_50')
        progress = [order.lifecycle_progress]
        self._send_and_approve(order)
        progress.append(order.lifecycle_progress)

        self._pay(order, request, order.amount_total / 2)
        progress.append(order.lifecycle_progress)
        self.assertEqual(order.lifecycle_stage, 'prepaid')

        self._receive(order)
        progress.append(order.lifecycle_progress)
        self.assertEqual(order.lifecycle_stage, 'in_stock')

        self._assert_monotonic(progress)
        self._maybe_check_completed(order, request)

    def test_post_payment_monotonic(self):
        order, request = self._make_order('post_payment')
        progress = [order.lifecycle_progress]
        self._send_and_approve(order)
        progress.append(order.lifecycle_progress)
        self.assertEqual(order.lifecycle_stage, 'approved')

        self._receive(order)
        progress.append(order.lifecycle_progress)
        self.assertEqual(order.lifecycle_stage, 'in_stock')

        self._assert_monotonic(progress)
        self._maybe_check_completed(order, request)


@tagged('post_install', '-at_install')
class TestExpectedArrivalDate(TransactionCase):
    """expected_arrival_date - отзыв пользователя 2026-07-23: "должна быть
    возможность увидеть ориентировочную дату прибытия". Берётся с первой
    связанной заявки (order.request_ids[:1].desired_date), тот же паттерн,
    что уже есть в базовом модуле для requester_id."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Arrival Vendor', 'vat': '7800000007',
        })
        cls.product = cls.env['product.product'].create({'name': 'Test Arrival Product'})

    def _make_order(self):
        return self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'name': self.product.name,
                'product_qty': 1, 'product_uom': self.product.uom_id.id, 'price_unit': 100.0,
            })],
        })

    def test_mirrors_first_request_desired_date(self):
        order = self._make_order()
        self.env['purchase.request'].create({
            'purchase_order_id': order.id,
            'desired_date': '2026-08-15',
            'line_ids': [(0, 0, {'name': self.product.name, 'product_qty': 1})],
        })
        order.invalidate_recordset()
        self.assertEqual(str(order.expected_arrival_date), '2026-08-15')

    def test_empty_without_desired_date(self):
        order = self._make_order()
        self.env['purchase.request'].create({
            'purchase_order_id': order.id,
            'line_ids': [(0, 0, {'name': self.product.name, 'product_qty': 1})],
        })
        order.invalidate_recordset()
        self.assertFalse(order.expected_arrival_date)

    def test_empty_without_any_request(self):
        order = self._make_order()
        self.assertFalse(order.expected_arrival_date)


@tagged('post_install', '-at_install')
class TestDateOnlyFields(TransactionCase):
    """deadline_date/planned_arrival_date - отзыв 2026-07-23: "уберите время,
    оставьте только дату". date_order/date_planned нативные и технически
    datetime - widget="date" во вьюхе на них не работает (Odoo игнорирует
    widget с несовпадающим типом), поэтому есть настоящие Date-поля поверх
    них."""

    def setUp(self):
        super().setUp()
        self.partner = self.env['res.partner'].create({
            'name': 'Test Date Only Vendor', 'vat': '7800000008',
        })
        self.product = self.env['product.product'].create({'name': 'Test Date Only Product'})

    def test_deadline_and_planned_arrival_strip_time(self):
        order = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'date_order': '2026-08-15 14:32:07',
            'date_planned': '2026-09-01 09:00:00',
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'name': self.product.name,
                'product_qty': 1, 'product_uom': self.product.uom_id.id, 'price_unit': 100.0,
            })],
        })
        self.assertEqual(str(order.deadline_date), '2026-08-15')
        self.assertEqual(str(order.planned_arrival_date), '2026-09-01')

    def test_empty_planned_arrival_when_not_set(self):
        # date_order имеет default=now() в базовом Odoo, но date_planned -
        # нет, поэтому именно на нём легко проверить пустую ветку без риска
        # напороться на скрытый default.
        order = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'name': self.product.name,
                'product_qty': 1, 'product_uom': self.product.uom_id.id, 'price_unit': 100.0,
            })],
        })
        order.date_planned = False
        order._compute_date_only_fields()
        self.assertFalse(order.planned_arrival_date)
