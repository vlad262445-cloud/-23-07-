from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPaymentRoutes(TransactionCase):
    """Стейт-машина заявки (purchase.request.state) для всех 3 маршрутов
    оплаты, включая момент появления кнопки "Оформить доставку".

    Обходит реальный вызов ИИ (payment_import_wizard.action_import) -
    action_confirm() от него не зависит, поэтому можно проверить всю логику
    подтверждения/продвижения статуса без сетевого вызова.

    can_arrange_delivery раньше считался неправильно (сравнение по позиции
    в списке вместо явной проверки approval_state + предыдущего шага) -
    баг был найден и исправлен 2026-07-09/10 именно на этих трёх маршрутах,
    поэтому регрессия здесь особенно важна.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.chief_buyer_user = cls.env['res.users'].create({
            'name': 'Test Route Chief Buyer',
            'login': 'test_route_chief_buyer',
            'email': 'test_route_chief_buyer@example.com',
            'groups_id': [(6, 0, [cls.env.ref('purchase_pdf_import.group_chief_buyer').id])],
        })
        cls.accountant_user = cls.env['res.users'].create({
            'name': 'Test Route Accountant',
            'login': 'test_route_accountant_2',
            'email': 'test_route_accountant_2@example.com',
            'groups_id': [(4, cls.env.ref('purchase_pdf_import.group_accountant').id)],
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Route Vendor', 'vat': '7800000001',
        })
        cls.product = cls.env['product.product'].create({'name': 'Test Route Product'})
        plan = cls.env['account.analytic.plan'].create({'name': 'Test Route Plan'})
        cls.analytic_account = cls.env['account.analytic.account'].create({
            'name': 'Test Route Analytic', 'plan_id': plan.id,
        })
        category_plan = cls.env['account.analytic.plan'].create({'name': 'Test Route Category Plan'})
        cls.analytic_category = cls.env['account.analytic.account'].create({
            'name': 'Test Route Category', 'plan_id': category_plan.id,
        })

    def _make_order(self, payment_type, requested_by=None):
        order = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'payment_type': payment_type,
            'cost_analytic_account_id': self.analytic_account.id,
            'cost_category_id': self.analytic_category.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'name': self.product.name,
                'product_qty': 10,
                'product_uom': self.product.uom_id.id,
                'price_unit': 100.0,
            })],
        })
        request_vals = {
            'purchase_order_id': order.id,
            'line_ids': [(0, 0, {'name': self.product.name, 'product_qty': 10})],
        }
        if requested_by:
            # requested_by указан прямо в create(), а не отдельным write()
            # после - заявка блокируется от правок сразу же, как только у
            # неё есть purchase_order_id (см. request lock, purchase_request.py),
            # а create() этой блокировке не подчиняется, в отличие от write().
            request_vals['requested_by'] = requested_by.id
        request = self.env['purchase.request'].create(request_vals)
        order.action_send_to_approval()
        # Может быть больше одного согласующего (если в базе, где гоняется
        # тест, уже есть другие реальные пользователи в группе "Главный
        # закупщик") - согласовываем от имени каждого, а не только своего
        # тестового пользователя, иначе _check_all_approved не сработает.
        for line in order.approval_line_ids.filtered(lambda item: item.state == 'pending'):
            line.with_user(line.approver_id).action_approve()
        order.invalidate_recordset()
        request.invalidate_recordset()
        return order, request

    def _validate_receipt(self, order):
        picking = self.env['stock.picking'].search([('group_id', '=', order.group_id.id)])
        picking.button_validate()

    def _confirm_payment(self, order, request, amount):
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

    def test_late_added_approver_gets_notified(self):
        # Боровику не пришла активность "Требуется согласование закупки" -
        # его добавили согласующим ПОСЛЕ того, как заказ уже отправили на
        # согласование (Владислав добавил его через список "Согласование"
        # уже после своего одобрения). action_send_to_approval рассылает
        # уведомления только один раз, в момент отправки - для строки,
        # созданной позже, ничего не вызывало _notify_approver вообще.
        # Заказ должен остаться в "На согласовании" (не все сразу
        # согласовывают, как в _make_order), поэтому строится вручную.
        order = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'payment_type': 'full_prepay',
            'cost_analytic_account_id': self.analytic_account.id,
            'cost_category_id': self.analytic_category.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'name': self.product.name,
                'product_qty': 10,
                'product_uom': self.product.uom_id.id,
                'price_unit': 100.0,
            })],
        })
        self.env['purchase.request'].create({
            'purchase_order_id': order.id,
            'line_ids': [(0, 0, {'name': self.product.name, 'product_qty': 10})],
        })
        order.action_send_to_approval()
        self.assertEqual(order.approval_state, 'to_approve')

        late_approver = self.env['res.users'].create({
            'name': 'Test Late Approver',
            'login': 'test_late_approver',
            'email': 'test_late_approver@example.com',
        })
        self.env['purchase.approval.line'].create({
            'purchase_order_id': order.id,
            'approver_id': late_approver.id,
        })
        activity = order.activity_ids.filtered(
            lambda a: a.summary == 'Требуется согласование закупки'
            and a.user_id == late_approver)
        self.assertTrue(
            activity,
            'согласующий, добавленный после отправки на согласование, тоже должен получить активность')

    def test_own_approval_activity_closes_immediately(self):
        # Владислав одобрил P00053/P00070 ещё вчера, но его собственная
        # активность "Требуется согласование закупки" осталась висеть -
        # закрывалась она только когда проголосуют ВСЕ (см.
        # _check_all_approved), и выглядело так, будто он ничего не сделал.
        # Активность конкретного согласующего должна закрываться сразу
        # после его собственного голоса, не дожидаясь остальных.
        order = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'payment_type': 'full_prepay',
            'cost_analytic_account_id': self.analytic_account.id,
            'cost_category_id': self.analytic_category.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'name': self.product.name,
                'product_qty': 10,
                'product_uom': self.product.uom_id.id,
                'price_unit': 100.0,
            })],
        })
        self.env['purchase.request'].create({
            'purchase_order_id': order.id,
            'line_ids': [(0, 0, {'name': self.product.name, 'product_qty': 10})],
        })
        second_approver = self.env['res.users'].create({
            'name': 'Test Second Approver',
            'login': 'test_second_approver',
            'email': 'test_second_approver@example.com',
        })
        self.env['purchase.approval.line'].create({
            'purchase_order_id': order.id,
            'approver_id': second_approver.id,
        })
        order.action_send_to_approval()
        self.assertEqual(order.approval_state, 'to_approve')

        chief_line = order.approval_line_ids.filtered(lambda l: l.approver_id == self.chief_buyer_user)
        chief_line.with_user(self.chief_buyer_user).action_approve()

        self.assertEqual(order.approval_state, 'to_approve', 'второй согласующий ещё не проголосовал')
        own_activity = order.activity_ids.filtered(
            lambda a: a.summary == 'Требуется согласование закупки' and a.user_id == self.chief_buyer_user)
        self.assertFalse(own_activity, 'активность того, кто уже одобрил, должна закрыться сразу')
        other_activity = order.activity_ids.filtered(
            lambda a: a.summary == 'Требуется согласование закупки' and a.user_id == second_approver)
        self.assertTrue(other_activity, 'активность того, кто ещё не проголосовал, должна остаться')

    def test_accountant_notified_after_approval(self):
        # Лариса Романова (Главный бухгалтер) сообщила, что не получает
        # никакого уведомления о согласованных закупках - раньше единственный
        # способ узнать было зайти в реестр и отфильтровать "Не хватает
        # документа". Проверяем, что активность реально создаётся и
        # закрывается после платежа, а не просто не падает с ошибкой.
        order, request = self._make_order('full_prepay')
        activity = order.activity_ids.filtered(
            lambda a: a.summary == 'Требуется прикрепить платёжку'
            and a.user_id == self.accountant_user)
        self.assertTrue(activity, 'бухгалтер должен получить напоминание после согласования')

        self._confirm_payment(order, request, order.amount_total)
        remaining = order.activity_ids.filtered(
            lambda a: a.summary == 'Требуется прикрепить платёжку')
        self.assertFalse(remaining, 'напоминание должно закрыться после реальной оплаты')

    def test_delivery_notifications_target_buyer_not_requester(self):
        # Мицуков Александр (обычный заявитель, роль "Мастер участка")
        # сообщил, что видит "оплачено", но ничего не видит про доставку -
        # разбор показал две причины: активность "нужно организовать
        # доставку" уходила на requested_by (у него нет ни доступа к заказу,
        # ни прав на кнопку "Оформить доставку"), а сообщение о том, что
        # доставка оформлена, писалось только в чат заказа, который
        # заявитель вообще не видит.
        requester = self.env['res.users'].create({
            'name': 'Test Route Requester',
            'login': 'test_route_requester',
            'email': 'test_route_requester@example.com',
        })
        order, request = self._make_order('full_prepay', requested_by=requester)
        order.user_id = self.chief_buyer_user

        self._confirm_payment(order, request, order.amount_total)
        activity = request.activity_ids.filtered(
            lambda a: 'организовать получение' in a.summary)
        self.assertEqual(activity.user_id, self.chief_buyer_user,
                          'активность должна уйти ответственному закупщику, а не заявителю')

        order.action_arrange_delivery()
        self.assertTrue(
            'Доставка оформлена' in request.message_ids[0].body,
            'заявитель должен увидеть в своей заявке, что доставка оформлена')

    def test_full_prepay_route(self):
        order, request = self._make_order('full_prepay')
        self.assertEqual(request.state, 'approved')
        self.assertFalse(order.can_arrange_delivery, 'до оплаты доставку оформлять рано')

        self._confirm_payment(order, request, order.amount_total)
        self.assertEqual(request.state, 'invoice_paid')
        self.assertTrue(order.can_arrange_delivery, 'после полной оплаты пора оформлять доставку')

        order.action_arrange_delivery()
        self.assertEqual(request.state, 'in_transit')
        self.assertFalse(order.can_arrange_delivery, 'повторно оформлять доставку не нужно')

    def test_split_50_50_route(self):
        order, request = self._make_order('split_50_50')
        self.assertEqual(request.state, 'approved')
        self.assertFalse(order.can_arrange_delivery, 'до предоплаты 50% доставку оформлять рано')

        half = order.amount_total / 2
        self._confirm_payment(order, request, half)
        self.assertEqual(request.state, 'partial_paid')
        self.assertTrue(order.can_arrange_delivery, 'после предоплаты 50% пора оформлять доставку')

        order.action_arrange_delivery()
        self.assertEqual(request.state, 'in_transit')
        self.assertFalse(order.can_arrange_delivery)

        self._validate_receipt(order)
        self.assertEqual(request.state, 'in_stock')

        self._confirm_payment(order, request, half)
        self.assertEqual(request.state, 'invoice_paid', 'вторые 50% должны закрыть оплату')

    def test_post_payment_route(self):
        order, request = self._make_order('post_payment')
        self.assertEqual(request.state, 'approved')
        self.assertTrue(
            order.can_arrange_delivery,
            'при оплате после получения доставку можно оформлять сразу после согласования')

        order.action_arrange_delivery()
        self.assertEqual(request.state, 'in_transit')
        self.assertFalse(order.can_arrange_delivery)

        self._validate_receipt(order)
        self.assertEqual(request.state, 'in_stock')

        self._confirm_payment(order, request, order.amount_total)
        self.assertEqual(request.state, 'invoice_paid')

    def test_payment_before_receipt_does_not_skip_delivery_milestones(self):
        # Реальный случай на P00003 (оплата после получения): бухгалтер
        # загрузил платёжку раньше, чем была подтверждена приёмка на
        # складе - статус тихо перепрыгнул "В пути"/"На складе" сразу на
        # "Счёт оплачен", после чего кнопка "Оформить доставку" пропала
        # навсегда (can_arrange_delivery для этого маршрута требует
        # request_state == 'approved', а он уже уехал дальше). Платёж
        # должен придержаться, пока приёмка не будет подтверждена, а не
        # проскакивать её молча.
        order, request = self._make_order('post_payment')
        self._confirm_payment(order, request, order.amount_total)
        self.assertEqual(
            request.state, 'approved',
            'платёж раньше приёмки не должен продвигать статус вперёд')
        self.assertTrue(
            order.can_arrange_delivery,
            'кнопка "Оформить доставку" не должна пропадать из-за раннего платежа')

        order.action_arrange_delivery()
        self._validate_receipt(order)
        self.assertEqual(
            request.state, 'invoice_paid',
            'как только приёмка подтверждена, уже загруженный платёж должен закрыть оплату')

    def test_arrange_delivery_requires_approval(self):
        # Регрессия конкретно под баг 2026-07-09: раньше can_arrange_delivery
        # мог включиться до реального согласования, если сравнение шло по
        # индексу в списке статусов, а не по approval_state.
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
                'price_unit': 1.0,
            })],
        })
        self.env['purchase.request'].create({
            'purchase_order_id': order.id,
            'line_ids': [(0, 0, {'name': self.product.name, 'product_qty': 1})],
        })
        order.action_send_to_approval()
        self.assertFalse(
            order.can_arrange_delivery,
            'заказ ещё не согласован - "Оформить доставку" не должна быть доступна')
