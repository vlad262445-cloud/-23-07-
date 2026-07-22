from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPdfImportManual(TransactionCase):
    """2026-07-15: внешний ИИ-шлюз (omniroute) несколько раз подряд оказался
    ненадёжен/недоступен, и заказ физически нельзя было оформить - "Оформить
    заказ" всегда требовал загруженный PDF и обращение к ИИ, без запасного
    варианта. "Оформить без ИИ" берёт позиции прямо из заявки (название +
    количество) без распознавания цены - человек дозаполняет её на заказе.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Manual Vendor', 'vat': '7800000003',
        })

    def _make_request(self, lines=None):
        lines = lines or [{'name': 'Test Manual Item', 'product_qty': 3}]
        return self.env['purchase.request'].create({
            'line_ids': [(0, 0, line) for line in lines],
        })

    def test_manual_creates_order_from_request_lines(self):
        request = self._make_request(lines=[
            {'name': 'Резец MTR-5', 'product_qty': 2},
            {'name': 'Резец MTR-6', 'product_qty': 5},
        ])
        wizard = self.env['purchase.pdf.import.wizard'].create({
            'vendor_id': self.partner.id,
            'payment_type': 'full_prepay',
            'request_id': request.id,
        })
        wizard.action_import_manual()

        order = wizard.result_order_id
        self.assertTrue(order, 'заказ должен быть создан')
        self.assertEqual(order.partner_id, self.partner)
        self.assertEqual(order.payment_type, 'full_prepay')
        self.assertEqual(len(order.order_line), 2)
        self.assertEqual(set(order.order_line.mapped('name')), {'Резец MTR-5', 'Резец MTR-6'})
        self.assertEqual(sorted(order.order_line.mapped('product_qty')), [2.0, 5.0])
        self.assertTrue(all(p == 0.0 for p in order.order_line.mapped('price_unit')),
                         'цена без ИИ неизвестна - должна остаться 0 для ручного заполнения')

        self.assertEqual(request.purchase_order_id, order)
        self.assertEqual(request.state, 'invoice_generated')

    def test_manual_corrects_existing_order_instead_of_duplicating(self):
        # Найдено ревью 2026-07-15: раньше action_import_manual создавал
        # НОВЫЙ заказ каждый раз, даже если у заявки уже был свой - молча
        # плодило дубли и отвязывало заявку от уже настроенного заказа.
        request = self._make_request(lines=[{'name': 'Test Manual Item', 'product_qty': 3}])
        wizard = self.env['purchase.pdf.import.wizard'].create({
            'vendor_id': self.partner.id,
            'payment_type': 'full_prepay',
            'request_id': request.id,
        })
        wizard.action_import_manual()
        first_order = wizard.result_order_id

        second_wizard = self.env['purchase.pdf.import.wizard'].create({
            'vendor_id': self.partner.id,
            'payment_type': 'full_prepay',
            'request_id': request.id,
        })
        second_wizard.action_import_manual()

        self.assertEqual(
            second_wizard.result_order_id, first_order,
            'повторное "Оформить без ИИ" должно обновить тот же заказ, а не создать новый')
        self.assertEqual(len(first_order.order_line), 1, 'старые строки должны быть заменены, а не задвоены')

    def test_manual_blocked_once_sent_to_approval(self):
        request = self._make_request()
        wizard = self.env['purchase.pdf.import.wizard'].create({
            'vendor_id': self.partner.id,
            'payment_type': 'full_prepay',
            'request_id': request.id,
        })
        wizard.action_import_manual()
        order = wizard.result_order_id

        plan = self.env['account.analytic.plan'].create({'name': 'Test Manual Plan'})
        account = self.env['account.analytic.account'].create({'name': 'Test Manual Analytic', 'plan_id': plan.id})
        order.write({'cost_analytic_account_id': account.id, 'cost_category_id': account.id})
        self.env['purchase.approval.line'].create({
            'purchase_order_id': order.id,
            'approver_id': self.env.user.id,
        })
        order.action_send_to_approval()

        third_wizard = self.env['purchase.pdf.import.wizard'].create({
            'vendor_id': self.partner.id,
            'payment_type': 'full_prepay',
            'request_id': request.id,
        })
        with self.assertRaises(UserError):
            third_wizard.action_import_manual()

    def test_manual_requires_vendor(self):
        request = self._make_request()
        wizard = self.env['purchase.pdf.import.wizard'].create({
            'payment_type': 'full_prepay',
            'request_id': request.id,
        })
        with self.assertRaises(UserError):
            wizard.action_import_manual()

    def test_manual_requires_request(self):
        wizard = self.env['purchase.pdf.import.wizard'].create({
            'vendor_id': self.partner.id,
            'payment_type': 'full_prepay',
        })
        with self.assertRaises(UserError):
            wizard.action_import_manual()

    def test_regular_import_requires_pdf_file(self):
        # pdf_file больше не required=True на самом поле (чтобы форма вообще
        # открывалась без файла и была видна кнопка "Оформить без ИИ") - но
        # обычный "Импортировать" по-прежнему должен требовать файл явной
        # проверкой в самом action_import.
        wizard = self.env['purchase.pdf.import.wizard'].create({
            'payment_type': 'full_prepay',
        })
        with self.assertRaises(UserError):
            wizard.action_import()
