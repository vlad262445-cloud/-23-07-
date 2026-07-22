from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPendingActionShort(TransactionCase):
    """_pending_action_key() разбирает ту же ситуацию, что и
    _compute_pending_action_note в базовом модуле - см. NOTES.md за тем,
    как ветки исходного метода свёрнуты в 8 значений короткого ярлыка."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Pending Vendor', 'vat': '7800000005',
        })
        cls.product = cls.env['product.product'].create({'name': 'Test Pending Product'})
        plan = cls.env['account.analytic.plan'].create({'name': 'Test Pending Plan'})
        cls.analytic_account = cls.env['account.analytic.account'].create({
            'name': 'Test Pending Analytic', 'plan_id': plan.id,
        })
        category_plan = cls.env['account.analytic.plan'].create({'name': 'Test Pending Category Plan'})
        cls.analytic_category = cls.env['account.analytic.account'].create({
            'name': 'Test Pending Category', 'plan_id': category_plan.id,
        })

    def _basic_order(self, **vals):
        base = {
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'name': self.product.name,
                'product_qty': 1, 'product_uom': self.product.uom_id.id, 'price_unit': 100.0,
            })],
        }
        base.update(vals)
        return self.env['purchase.order'].create(base)

    # --- ветки, не завязанные на compute-поля (state/approval_state/поля) --

    def test_cancelled_order(self):
        order = self._basic_order(state='cancel')
        self.assertEqual(order._pending_action_key(), 'none')

    def test_declined_order(self):
        order = self._basic_order(approval_state='declined')
        self.assertEqual(order._pending_action_key(), 'declined')

    def test_missing_fields(self):
        order = self._basic_order(approval_state='none', payment_type=False)
        self.assertEqual(order._pending_action_key(), 'fill_fields')

    def test_ready_to_send_maps_to_to_approve(self):
        order = self._basic_order(
            approval_state='none', payment_type='full_prepay',
            cost_analytic_account_id=self.analytic_account.id,
            cost_category_id=self.analytic_category.id,
        )
        self.assertEqual(order._pending_action_key(), 'to_approve')

    def test_waiting_for_approval(self):
        order = self._basic_order(approval_state='to_approve')
        self.assertEqual(order._pending_action_key(), 'to_approve')

    # --- ветки после согласования - нужен реальный проход через flow -------

    def _make_approved_order(self):
        order = self._basic_order(
            payment_type='post_payment',
            cost_analytic_account_id=self.analytic_account.id,
            cost_category_id=self.analytic_category.id,
        )
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

    def test_arrange_delivery(self):
        order, request = self._make_approved_order()
        self.assertTrue(order.can_arrange_delivery)
        self.assertEqual(order._pending_action_key(), 'arrange_delivery')

    def test_upload_updd(self):
        order, request = self._make_approved_order()
        order.action_arrange_delivery()
        picking = self.env['stock.picking'].search([('group_id', '=', order.group_id.id)])
        picking.button_validate()
        order.invalidate_recordset()
        self.assertTrue(order.updd_relevant)
        self.assertFalse(order.updd_line_ids)
        self.assertEqual(order._pending_action_key(), 'upload_updd')

    def _reach_in_stock(self):
        order, request = self._make_approved_order()
        order.action_arrange_delivery()
        picking = self.env['stock.picking'].search([('group_id', '=', order.group_id.id)])
        picking.button_validate()
        self.env['purchase.updd.line'].create({
            'purchase_order_id': order.id, 'seller_inn': self.partner.vat, 'partner_matched': True,
        })
        order.invalidate_recordset()
        return order, request

    def test_pay(self):
        order, request = self._reach_in_stock()
        self.assertEqual(order.document_status, 'blocked')
        self.assertEqual(order.amount_paid, 0.0)
        self.assertEqual(order._pending_action_key(), 'pay')

    def test_surcharge(self):
        order, request = self._reach_in_stock()
        wizard = self.env['purchase.payment.import.wizard'].create({
            'purchase_order_id': order.id, 'request_id': request.id,
            'recognized_amount': order.amount_total * 0.4,
            'recognized_recipient_inn': self.partner.vat, 'partner_expected_inn': self.partner.vat,
            'expected_amount': order.amount_total, 'state': 'recognized',
        })
        wizard.action_confirm()
        order.invalidate_recordset()
        self.assertTrue(0 < order.amount_paid < order.amount_total * 0.95)
        self.assertEqual(order._pending_action_key(), 'surcharge')

    def test_none_when_fully_done(self):
        order, request = self._reach_in_stock()
        wizard = self.env['purchase.payment.import.wizard'].create({
            'purchase_order_id': order.id, 'request_id': request.id,
            'recognized_amount': order.amount_total,
            'recognized_recipient_inn': self.partner.vat, 'partner_expected_inn': self.partner.vat,
            'expected_amount': order.amount_total, 'state': 'recognized',
        })
        wizard.action_confirm()
        order.invalidate_recordset()
        self.assertEqual(order.document_status, 'done')
        self.assertEqual(order._pending_action_key(), 'none')

    def test_stored_field_matches_key(self):
        order, request = self._make_approved_order()
        order.invalidate_recordset()
        self.assertEqual(order.pending_action_short, order._pending_action_key())
