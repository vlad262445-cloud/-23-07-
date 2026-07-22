from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPendingActionNote(TransactionCase):
    """Владислав (Главный закупщик) смотрел стандартный список Odoo "Заказы
    на закупку" (не наш "Реестр закупок") и не понимал, требуется ли от него
    действие - там виден только родной статус Odoo, который никак не
    отражает наш процесс согласования/оплаты/УПД. pending_action_note - одна
    короткая строка "что и от кого требуется прямо сейчас", проверяем её на
    каждом значимом шаге процесса.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.chief_buyer_user = cls.env['res.users'].create({
            'name': 'Test Pending Chief Buyer',
            'login': 'test_pending_chief_buyer',
            'email': 'test_pending_chief_buyer@example.com',
            'groups_id': [(6, 0, [cls.env.ref('purchase_pdf_import.group_chief_buyer').id])],
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Pending Vendor', 'vat': '7800000002',
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

    def _make_order(self, payment_type='post_payment', configured=False):
        vals = {
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'name': self.product.name,
                'product_qty': 1,
                'product_uom': self.product.uom_id.id,
                'price_unit': 100.0,
            })],
        }
        if configured:
            vals.update({
                'payment_type': payment_type,
                'cost_analytic_account_id': self.analytic_account.id,
                'cost_category_id': self.analytic_category.id,
            })
        order = self.env['purchase.order'].create(vals)
        # can_arrange_delivery/request_state читают request_ids - без
        # привязанной заявки они никогда не станут true, даже если сам заказ
        # полностью настроен и оплачен.
        request = self.env['purchase.request'].create({
            'purchase_order_id': order.id,
            'line_ids': [(0, 0, {'name': self.product.name, 'product_qty': 1})],
        })
        return order, request

    def test_missing_required_fields_lists_them(self):
        order, _request = self._make_order(configured=False)
        note = order.pending_action_note
        self.assertIn('статью затрат', note)
        self.assertIn('категорию', note)
        self.assertIn('тип оплаты', note)

    def test_ready_to_send_when_configured_but_not_sent(self):
        order, _request = self._make_order(configured=True)
        self.assertEqual(order.pending_action_note, 'Закупщику: отправить на согласование.')

    def test_to_approve_lists_pending_approvers(self):
        order, _request = self._make_order(configured=True)
        order.action_send_to_approval()
        self.assertIn(self.chief_buyer_user.name, order.pending_action_note)
        self.assertIn('Ожидает согласования', order.pending_action_note)

    def test_declined_shows_reason(self):
        order, _request = self._make_order(configured=True)
        order.action_send_to_approval()
        line = order.approval_line_ids.filtered(lambda l: l.approver_id == self.chief_buyer_user)
        line.with_user(self.chief_buyer_user).write({'comment': 'Слишком дорого'})
        line.with_user(self.chief_buyer_user).action_refuse()
        self.assertIn('Слишком дорого', order.pending_action_note)

    def test_can_arrange_delivery_after_full_prepay(self):
        order, request = self._make_order(payment_type='full_prepay', configured=True)
        order.action_send_to_approval()
        for line in order.approval_line_ids.filtered(lambda item: item.state == 'pending'):
            line.with_user(line.approver_id).action_approve()
        wizard = self.env['purchase.payment.import.wizard'].create({
            'purchase_order_id': order.id,
            'request_id': request.id,
            'recognized_amount': order.amount_total,
            'recognized_recipient_inn': self.partner.vat,
            'partner_expected_inn': self.partner.vat,
            'expected_amount': order.amount_total,
            'state': 'recognized',
        })
        wizard.action_confirm()
        self.assertEqual(order.pending_action_note, 'Закупщику: оформить доставку.')

    def test_note_clears_once_fully_done(self):
        order, request = self._make_order(payment_type='full_prepay', configured=True)
        order.action_send_to_approval()
        for line in order.approval_line_ids.filtered(lambda item: item.state == 'pending'):
            line.with_user(line.approver_id).action_approve()
        wizard = self.env['purchase.payment.import.wizard'].create({
            'purchase_order_id': order.id,
            'request_id': request.id,
            'recognized_amount': order.amount_total,
            'recognized_recipient_inn': self.partner.vat,
            'partner_expected_inn': self.partner.vat,
            'expected_amount': order.amount_total,
            'state': 'recognized',
        })
        wizard.action_confirm()
        order.action_arrange_delivery()
        picking = self.env['stock.picking'].search([('group_id', '=', order.group_id.id)])
        picking.button_validate()
        # Реальная привязка УПД (а не "Пропустить") - _compute_document_status
        # намеренно продолжает считать "пропущенный" УПД недостающим документом
        # (напоминание, что его всё равно нужно прикрепить позже), поэтому
        # для проверки "всё сделано, действий не нужно" нужен настоящий УПД.
        self.env['purchase.updd.line'].create({'purchase_order_id': order.id})
        self.assertFalse(
            order.pending_action_note,
            'после доставки, оплаты и прикреплённого УПД действий быть не должно')
