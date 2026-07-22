import base64
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

FAKE_EXTRACTION = {
    'vendor': {'name': 'Test Correction Vendor'},
    'invoice_number': 'INV-1',
    'currency': 'RUB',
    'items': [{'name': 'Test Correction Item', 'quantity': 2, 'unit_price': 100.0}],
}

FAKE_EXTRACTION_CORRECTED = {
    'vendor': {'name': 'Test Correction Vendor'},
    'invoice_number': 'INV-1-corrected',
    'currency': 'RUB',
    'items': [{'name': 'Test Correction Item', 'quantity': 3, 'unit_price': 150.0}],
}


@tagged('post_install', '-at_install')
class TestPdfImportCorrection(TransactionCase):
    """P00131: у Ольги не было способа перезагрузить счёт после того, как
    поставщик прислал исправленный документ - кнопка "Оформить заказ"
    пропадает сразу после создания заказа, обратного пути не было (реальный
    случай закончился тем, что позиции заказа были обнулены вручную).
    Повторная загрузка должна обновлять уже существующий заказ вместо
    ошибки/дубликата - но только пока заказ ещё не отправлен на
    согласование, иначе можно молча подменить то, что кто-то уже одобрил.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.icp = cls.env['ir.config_parameter'].sudo()
        cls.icp.set_param('purchase_pdf_import.anthropic_api_key', 'test-key')
        cls.chief_buyer_user = cls.env['res.users'].create({
            'name': 'Test Correction Chief Buyer',
            'login': 'test_correction_chief_buyer',
            'email': 'test_correction_chief_buyer@example.com',
            'groups_id': [(6, 0, [cls.env.ref('purchase_pdf_import.group_chief_buyer').id])],
        })
        plan = cls.env['account.analytic.plan'].create({'name': 'Test Correction Plan'})
        cls.analytic_account = cls.env['account.analytic.account'].create({
            'name': 'Test Correction Analytic', 'plan_id': plan.id,
        })
        category_plan = cls.env['account.analytic.plan'].create({'name': 'Test Correction Category Plan'})
        cls.analytic_category = cls.env['account.analytic.account'].create({
            'name': 'Test Correction Category', 'plan_id': category_plan.id,
        })

    def _import(self, request, extraction):
        wizard = self.env['purchase.pdf.import.wizard'].create({
            'pdf_file': base64.b64encode(b'%PDF-1.4 fake'),
            'pdf_filename': 'invoice.pdf',
            'payment_type': 'full_prepay',
            'request_id': request.id,
        })
        with patch(
            'odoo.addons.purchase_pdf_import.wizard.pdf_import_wizard.extract_text_from_pdf',
            return_value='some invoice text',
        ), patch(
            'odoo.addons.purchase_pdf_import.wizard.pdf_import_wizard.pdf_is_scanned',
            return_value=False,
        ), patch(
            'odoo.addons.purchase_pdf_import.wizard.pdf_import_wizard.call_llm',
            return_value=extraction,
        ):
            wizard.action_import()
        return wizard

    def _make_request(self):
        return self.env['purchase.request'].create({
            'line_ids': [(0, 0, {'name': 'Test Correction Item', 'product_qty': 2})],
        })

    def test_reimport_updates_existing_order_instead_of_creating_new(self):
        request = self._make_request()
        first_wizard = self._import(request, FAKE_EXTRACTION)
        order = first_wizard.result_order_id
        self.assertEqual(len(order.order_line), 1)
        self.assertEqual(order.order_line.product_qty, 2)
        self.assertEqual(order.order_line.price_unit, 100.0)

        second_wizard = self._import(request, FAKE_EXTRACTION_CORRECTED)
        self.assertEqual(
            second_wizard.result_order_id, order,
            'повторная загрузка должна обновить тот же заказ, а не создать новый')
        self.assertEqual(len(order.order_line), 1, 'старые позиции должны быть заменены, а не добавлены к новым')
        self.assertEqual(order.order_line.product_qty, 3)
        self.assertEqual(order.order_line.price_unit, 150.0)
        self.assertEqual(order.partner_ref, 'INV-1-corrected')
        self.assertTrue(
            any('скорректирован' in m.body for m in order.message_ids),
            'в чате должно быть видно, что счёт был именно скорректирован повторным импортом')

    def test_reimport_blocked_once_sent_to_approval(self):
        request = self._make_request()
        wizard = self._import(request, FAKE_EXTRACTION)
        order = wizard.result_order_id
        order.write({
            'cost_analytic_account_id': self.analytic_account.id,
            'cost_category_id': self.analytic_category.id,
        })
        # purchase.order.create() уже добавил всех "Главный закупщик" в
        # approval_line_ids автоматически (см. _add_chief_buyer_approval_lines
        # в purchase_order.py) - создавать ещё одну строку для того же
        # согласующего вручную не нужно и упадёт на unique-констрейнте.
        self.assertIn(self.chief_buyer_user, order.approval_line_ids.approver_id)
        order.action_send_to_approval()

        with self.assertRaises(UserError):
            self._import(request, FAKE_EXTRACTION_CORRECTED)

    def test_reimport_blocked_after_confirm_with_empty_approval_line_ids(self):
        # Баг найден ревью 2026-07-15: если на момент создания заказа в
        # группе "Главный закупщик" никого не было, approval_line_ids
        # остаётся пустым и approval_state так и застревает на 'none'
        # навсегда - button_confirm() пропускает проверку согласования,
        # когда approval_line_ids пуст (см. purchase_order.py), и заказ
        # можно подтвердить напрямую, минуя согласование. Старая проверка
        # полагалась только на approval_state != 'none' и пропускала
        # повторную загрузку счёта поверх уже подтверждённого заказа.
        # Тест гоняется поверх реальной базы - в группе может быть не только
        # cls.chief_buyer_user, но и настоящие сотрудники с ролью "Главный
        # закупщик" (см. test_reimport_blocked_once_sent_to_approval выше),
        # так что убрать нужно всех, а не только тестового пользователя.
        chief_buyer_group = self.env.ref('purchase_pdf_import.group_chief_buyer')
        chief_buyer_group.users.write({'groups_id': [(3, chief_buyer_group.id)]})
        request = self._make_request()
        wizard = self._import(request, FAKE_EXTRACTION)
        order = wizard.result_order_id
        self.assertFalse(order.approval_line_ids, 'без Главного закупщика согласующих быть не должно')
        order.write({
            'cost_analytic_account_id': self.analytic_account.id,
            'cost_category_id': self.analytic_category.id,
        })
        order.button_confirm()
        self.assertEqual(order.approval_state, 'none')
        self.assertIn(order.state, ('purchase', 'done'))

        with self.assertRaises(UserError):
            self._import(request, FAKE_EXTRACTION_CORRECTED)

    def test_reimport_recovers_cancelled_order(self):
        # Реальный случай на P00131: не имея штатного способа скорректировать
        # счёт, Ольга нажала стандартную "Отмена" на заказе, пытаясь начать
        # заново - заказ застрял в статусе "Отменён" с пустыми позициями и
        # суммой 0. Повторная загрузка должна сама вернуть такой заказ в
        # черновик и записать новые позиции, а не требовать разбираться с
        # отменённым заказом вручную.
        request = self._make_request()
        wizard = self._import(request, FAKE_EXTRACTION)
        order = wizard.result_order_id
        order.button_cancel()
        self.assertEqual(order.state, 'cancel')

        self._import(request, FAKE_EXTRACTION_CORRECTED)
        self.assertEqual(order.state, 'draft', 'заказ должен вернуться в черновик, а не остаться отменённым')
        self.assertEqual(len(order.order_line), 1)
        self.assertEqual(order.order_line.product_qty, 3)
        self.assertEqual(order.order_line.price_unit, 150.0)

    def test_reimport_recovers_declined_and_cancelled_order(self):
        # Реальный случай на P00948: Главный закупщик отклонил заказ
        # ("проблема с номенклатурой" - ИИ сам предупреждал об OCR-искажениях
        # в названии одной из позиций), после чего Ольга нажала стандартную
        # "Отменить", пытаясь начать заново - и застряла: старая проверка
        # блокировала повторную загрузку при ЛЮБОМ approval_state != 'none',
        # включая 'declined', хотя отклонение - это явный сигнал "надо
        # переделать", а не "кто-то мог уже одобрить".
        request = self._make_request()
        wizard = self._import(request, FAKE_EXTRACTION)
        order = wizard.result_order_id
        order.write({
            'cost_analytic_account_id': self.analytic_account.id,
            'cost_category_id': self.analytic_category.id,
        })
        order.action_send_to_approval()
        approval_line = order.approval_line_ids.filtered(lambda l: l.approver_id == self.chief_buyer_user)
        approval_line.with_user(self.chief_buyer_user).write({'comment': 'проблема с номенклатурой'})
        approval_line.with_user(self.chief_buyer_user).action_refuse()
        self.assertEqual(order.approval_state, 'declined')
        order.button_cancel()
        self.assertEqual(order.state, 'cancel')

        self._import(request, FAKE_EXTRACTION_CORRECTED)
        self.assertEqual(order.state, 'draft', 'заказ должен вернуться в черновик после повторной загрузки')
        self.assertEqual(len(order.order_line), 1, 'старые позиции должны быть заменены, а не добавлены к новым')
        self.assertEqual(order.order_line.product_qty, 3)
        self.assertEqual(order.order_line.price_unit, 150.0)
