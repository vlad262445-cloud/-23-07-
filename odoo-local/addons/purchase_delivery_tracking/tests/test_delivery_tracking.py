from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDeliveryTrackingSetup(TransactionCase):
    """Настройка заказа, готового к 'Оформить доставку' (can_arrange_delivery
    == True) - тот же рецепт, что и в purchase_pdf_import/tests/
    test_payment_routes.py: маршрут post_payment даёт доступ к кнопке сразу
    после согласования, без отдельного шага оплаты."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Delivery Vendor', 'vat': '7800000002',
        })
        cls.product = cls.env['product.product'].create({'name': 'Test Delivery Product'})
        plan = cls.env['account.analytic.plan'].create({'name': 'Test Delivery Plan'})
        cls.analytic_account = cls.env['account.analytic.account'].create({
            'name': 'Test Delivery Analytic', 'plan_id': plan.id,
        })
        category_plan = cls.env['account.analytic.plan'].create({'name': 'Test Delivery Category Plan'})
        cls.analytic_category = cls.env['account.analytic.account'].create({
            'name': 'Test Delivery Category', 'plan_id': category_plan.id,
        })
        cls.method_no_tracking = cls.env['purchase.delivery.method'].create({
            'name': 'Test Самовывоз', 'has_tracking': False,
        })
        cls.method_with_tracking = cls.env['purchase.delivery.method'].create({
            'name': 'Test СДЭК', 'has_tracking': True,
        })

    def _make_order(self):
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
                'price_unit': 100.0,
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
        self.assertTrue(order.can_arrange_delivery, 'заказ должен быть готов к оформлению доставки')
        return order, request

    def test_wizard_confirms_without_tracking_number_when_not_expected(self):
        order, request = self._make_order()
        wizard = self.env['purchase.delivery.tracking.wizard'].create({
            'purchase_order_id': order.id,
            'delivery_method_id': self.method_no_tracking.id,
        })
        wizard.action_confirm()

        self.assertEqual(order.delivery_method_id, self.method_no_tracking)
        self.assertFalse(order.tracking_number)
        self.assertFalse(order.can_arrange_delivery, 'доставка уже оформлена - кнопка должна пропасть')

    def test_switching_method_does_not_clear_already_entered_number(self):
        order, request = self._make_order()
        wizard = self.env['purchase.delivery.tracking.wizard'].create({
            'purchase_order_id': order.id,
            'delivery_method_id': self.method_with_tracking.id,
            'tracking_number': '1234567890',
        })
        # Переключение способа на тот, что скрывает поле номера в форме -
        # само значение поля (на уровне записи) не должно стираться: скрытие
        # чисто визуальное (invisible), очистки поля нигде не запрограммировано.
        wizard.delivery_method_id = self.method_no_tracking
        self.assertEqual(wizard.tracking_number, '1234567890')

    def test_chatter_message_with_tracking_number(self):
        order, request = self._make_order()
        wizard = self.env['purchase.delivery.tracking.wizard'].create({
            'purchase_order_id': order.id,
            'delivery_method_id': self.method_with_tracking.id,
            'tracking_number': '1234567890',
        })
        wizard.action_confirm()

        order_messages = order.message_ids.mapped('body')
        request_messages = request.message_ids.mapped('body')
        self.assertTrue(any('Test СДЭК' in b and '1234567890' in b for b in order_messages))
        self.assertTrue(any('Test СДЭК' in b and '1234567890' in b for b in request_messages),
                         'заявитель не имеет доступа к заказу - сообщение должно попасть и в заявку')

    def test_chatter_message_without_tracking_number(self):
        order, request = self._make_order()
        wizard = self.env['purchase.delivery.tracking.wizard'].create({
            'purchase_order_id': order.id,
            'delivery_method_id': self.method_no_tracking.id,
        })
        wizard.action_confirm()

        order_messages = order.message_ids.mapped('body')
        self.assertTrue(any('Test Самовывоз' in b for b in order_messages))
        self.assertFalse(any('трек-номер' in b for b in order_messages),
                          'без номера в сообщении не должно быть слова "трек-номер"')

    def test_arrange_delivery_without_method_does_not_post_extra_message(self):
        # Регрессия: существующие сценарии (в т.ч. тесты базового модуля),
        # где action_arrange_delivery вызывается напрямую без wizard'а и без
        # delivery_method_id, не должны получать наше сообщение вообще.
        order, request = self._make_order()
        count_before = len(order.message_ids)
        order.action_arrange_delivery()
        new_bodies = order.message_ids[:len(order.message_ids) - count_before].mapped('body')
        self.assertFalse(any('СДЭК' in b or 'Самовывоз' in b or 'трек-номер' in b for b in new_bodies))

    def test_quick_create_does_not_duplicate_on_repeated_name(self):
        Method = self.env['purchase.delivery.method']
        count_before = Method.search_count([])
        first_id, _name = Method.name_create('Быстрый Тест Способ')
        second_id, _name2 = Method.name_create('быстрый тест способ')
        self.assertEqual(first_id, second_id, 'повторный ввод того же названия не должен создавать дубль')
        self.assertEqual(Method.search_count([]), count_before + 1)

    def test_tracking_url_built_for_known_carrier_with_code_like_number(self):
        order, request = self._make_order()
        cdek = self.env['purchase.delivery.method'].create({'name': 'СДЭК', 'has_tracking': True})
        order.write({'delivery_method_id': cdek.id, 'tracking_number': '1234567890'})
        self.assertIn('1234567890', order.tracking_url)

    def test_tracking_url_empty_for_free_text_number(self):
        order, request = self._make_order()
        cdek = self.env['purchase.delivery.method'].create({'name': 'СДЭК', 'has_tracking': True})
        order.write({
            'delivery_method_id': cdek.id,
            'tracking_number': 'водитель поставщика, приедет в четверг',
        })
        self.assertFalse(order.tracking_url)

    def test_tracking_url_empty_for_unknown_carrier(self):
        order, request = self._make_order()
        order.write({'delivery_method_id': self.method_with_tracking.id, 'tracking_number': '1234567890'})
        self.assertFalse(order.tracking_url, '"Test СДЭК" не входит в справочник шаблонов - точное имя не совпадает')

    def test_delivery_summary(self):
        order, request = self._make_order()
        self.assertFalse(order.delivery_summary)

        order.delivery_method_id = self.method_no_tracking
        self.assertEqual(order.delivery_summary, 'Test Самовывоз')

        order.tracking_number = '1234567890'
        self.assertEqual(order.delivery_summary, 'Test Самовывоз · 1234567890')

    def test_request_form_fields_are_readonly_related(self):
        order, request = self._make_order()
        order.write({'delivery_method_id': self.method_with_tracking.id, 'tracking_number': 'ABC123'})
        self.assertEqual(request.delivery_method_id, self.method_with_tracking)
        self.assertEqual(request.tracking_number, 'ABC123')
        field = request._fields['delivery_method_id']
        self.assertTrue(field.related, 'поле должно быть related, а не собственным - readonly гарантируется моделью')

    def test_open_wizard_action_from_order_and_request(self):
        order, request = self._make_order()
        order_action = order.action_open_delivery_tracking_wizard()
        self.assertEqual(order_action['res_model'], 'purchase.delivery.tracking.wizard')
        self.assertEqual(order_action['context']['default_purchase_order_id'], order.id)

        request_action = request.action_open_delivery_tracking_wizard()
        self.assertEqual(request_action['res_model'], 'purchase.delivery.tracking.wizard')
        self.assertEqual(request_action['context']['default_purchase_order_id'], order.id)
