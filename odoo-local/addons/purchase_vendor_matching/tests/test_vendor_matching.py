import base64
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

# Валидные тестовые ИНН, посчитанные по тому же алгоритму, что в
# inn_utils.is_valid_inn (см. вывод скрипта в сессии разработки) - не из
# боевых данных (те заняты реальными поставщиками), но настоящие по
# контрольной сумме, а не случайные строки.
VALID_INN_A = '5001007304'          # использован для "найден по ИНН"
VALID_INN_B = '7700001235'          # использован для "создание нового"
BROKEN_INN_A = '5001007305'         # VALID_INN_A с испорченной контрольной цифрой


@tagged('post_install', '-at_install')
class TestVendorMatchingByInn(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.existing_partner = cls.env['res.partner'].create({
            'name': 'ООО "Ромашка"',
            'vat': VALID_INN_A,
            'supplier_rank': 1,
            'company_type': 'company',
        })

    def _vendor_data(self, name, tax_id=None, **extra):
        data = {'name': name}
        if tax_id is not None:
            data['tax_id'] = tax_id
        data.update(extra)
        return data

    # --- PDF-мастер --------------------------------------------------

    def test_matches_by_inn_despite_name_differences(self):
        wizard = self.env['purchase.pdf.import.wizard'].new({})
        for variant_name in (
            'ромашка ооо',
            '"Ромашка"',
            'ООО Ромашка',
            '  ООО "Ромашка"  ',
        ):
            partner = wizard._find_or_create_vendor(self._vendor_data(variant_name, VALID_INN_A))
            self.assertEqual(partner, self.existing_partner,
                              f'должен найтись по ИНН несмотря на написание "{variant_name}"')

    def test_note_set_only_when_name_differs(self):
        wizard = self.env['purchase.pdf.import.wizard'].new({})

        wizard._find_or_create_vendor(self._vendor_data('ООО Ромашка-Другое', VALID_INN_A))
        self.assertTrue(wizard.vendor_match_note, 'разное название - примечание должно появиться')
        self.assertIn(VALID_INN_A, wizard.vendor_match_note)

        wizard._find_or_create_vendor(self._vendor_data('ооо "ромашка"', VALID_INN_A))
        self.assertFalse(wizard.vendor_match_note, 'совпадающее (с точностью до регистра) название - без примечания')

    def test_invalid_inn_falls_back_to_name_search(self):
        wizard = self.env['purchase.pdf.import.wizard'].new({})
        # BROKEN_INN_A совпадает по цифрам почти с VALID_INN_A, но не проходит
        # контрольную сумму - поиск по ИНН обязан промолчать и уйти на старое
        # поведение (поиск по точному имени).
        partner = wizard._find_or_create_vendor(
            self._vendor_data('ООО "Ромашка"', BROKEN_INN_A))
        self.assertEqual(partner, self.existing_partner)
        self.assertFalse(wizard.vendor_match_note,
                          'поставщик найден по имени, а не по ИНН - примечания быть не должно')

    def test_real_prod_defect_inn_falls_back_to_name_search(self):
        # Реальный брак прода (см. п. 9.1 ТЗ и test_inn_utils): ИНН
        # "7801234567" уже лежит в базе у одного из поставщиков, но не
        # проходит контрольную сумму. Убеждаемся, что даже раз оказавшись в
        # базе, такой ИНН не используется для сопоставления.
        other = self.env['res.partner'].create({
            'name': 'ООО "Микрон Электро (тест)"', 'vat': '7801234567', 'supplier_rank': 1,
        })
        wizard = self.env['purchase.pdf.import.wizard'].new({})
        partner = wizard._find_or_create_vendor(
            self._vendor_data('ООО "Микрон Электро (тест)"', '7801234567'))
        self.assertEqual(partner, other)
        self.assertFalse(wizard.vendor_match_note)

    def test_new_vendor_created_with_normalized_vat(self):
        wizard = self.env['purchase.pdf.import.wizard'].new({})
        partner = wizard._find_or_create_vendor(
            self._vendor_data('ООО Новый Поставщик', f'ИНН {VALID_INN_A[:5]} {VALID_INN_B}'))
        # значение 'ИНН 50010 7700001235' содержит VALID_INN_B где-то внутри
        # мусора - после normalize_inn это уже другое число, поэтому тут
        # просто проверяем, что итоговый vat - только цифры, без пробелов/букв.
        self.assertTrue(partner.vat.isdigit())
        self.assertNotIn(' ', partner.vat)
        self.assertEqual(partner.name, 'ООО Новый Поставщик')
        self.assertEqual(partner.supplier_rank, 1)

    def test_only_empty_fields_filled_existing_not_overwritten(self):
        partner = self.env['res.partner'].create({
            'name': 'ООО "Полевой Тест"',
            'vat': VALID_INN_B,
            'phone': '+7 000 000-00-00',
            'supplier_rank': 1,
        })
        wizard = self.env['purchase.pdf.import.wizard'].new({})
        found = wizard._find_or_create_vendor(self._vendor_data(
            'ООО "Полевой Тест"', VALID_INN_B,
            phone='+7 111 111-11-11', email='new@example.com',
        ))
        self.assertEqual(found, partner)
        self.assertEqual(found.phone, '+7 000 000-00-00', 'уже заполненный телефон нельзя перетирать')
        self.assertEqual(found.email, 'new@example.com', 'пустой email должен дозаполниться')

    def test_ambiguous_inn_two_branches_does_not_merge(self):
        # Два разных филиала одного юрлица: общий ИНН, разные КПП. Без КПП
        # из документа выбрать правильный нечем - _match_vendor_by_inn не
        # должен угадывать (см. NOTES.md и комментарий в vendor_matching_mixin).
        branch_a = self.env['res.partner'].create({
            'name': 'ООО "Филиал А"', 'vat': VALID_INN_B, 'kpp': '780101001', 'supplier_rank': 1,
        })
        branch_b = self.env['res.partner'].create({
            'name': 'ООО "Филиал Б"', 'vat': VALID_INN_B, 'kpp': '997701001', 'supplier_rank': 1,
        })
        wizard = self.env['purchase.pdf.import.wizard'].new({})
        partner, note = wizard._match_vendor_by_inn(VALID_INN_B, 'ООО "Филиал А"')
        self.assertFalse(partner, 'при неоднозначном ИНН нельзя выбирать наугад ни один из филиалов')
        self.assertIsNone(note)
        self.assertNotEqual(branch_a, branch_b)

    # --- УПД-мастер (другая сигнатура) --------------------------------

    def test_updd_wizard_matches_by_inn(self):
        wizard = self.env['purchase.updd.import.wizard'].new({})
        partner = wizard._find_or_create_vendor('ромашка ооо', VALID_INN_A)
        self.assertEqual(partner, self.existing_partner)

    def test_updd_wizard_invalid_inn_falls_back(self):
        wizard = self.env['purchase.updd.import.wizard'].new({})
        partner = wizard._find_or_create_vendor('ООО "Ромашка"', BROKEN_INN_A)
        self.assertEqual(partner, self.existing_partner)

    def test_updd_wizard_creates_new_with_normalized_vat(self):
        wizard = self.env['purchase.updd.import.wizard'].new({})
        partner = wizard._find_or_create_vendor('ООО УПД Новый', f'ИНН {VALID_INN_B}')
        self.assertEqual(partner.vat, VALID_INN_B)
        self.assertEqual(partner.supplier_rank, 1)


@tagged('post_install', '-at_install')
class TestVendorMatchingActionImportIntegration(TransactionCase):
    """Проверяет весь action_import целиком (с моками вызова ИИ), а не
    только _find_or_create_vendor - чтобы убедиться, что обёртка над
    action_import действительно проставляет примечание в чат ИМЕННО
    созданного заказа, а не теряет его по дороге."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param(
            'purchase_pdf_import.anthropic_api_key', 'test-key-not-real')
        cls.existing_partner = cls.env['res.partner'].create({
            'name': 'ООО "Ромашка"', 'vat': VALID_INN_A, 'supplier_rank': 1,
        })

    def _make_wizard(self):
        return self.env['purchase.pdf.import.wizard'].create({
            'pdf_file': base64.b64encode(b'%PDF-1.4 fake test content'),
            'pdf_filename': 'test.pdf',
            'payment_type': 'full_prepay',
        })

    @patch('odoo.addons.purchase_pdf_import.wizard.pdf_import_wizard.call_llm')
    @patch('odoo.addons.purchase_pdf_import.wizard.pdf_import_wizard.pdf_is_scanned')
    @patch('odoo.addons.purchase_pdf_import.wizard.pdf_import_wizard.extract_text_from_pdf')
    def test_order_chatter_gets_inn_match_note(self, mock_extract, mock_scanned, mock_llm):
        mock_extract.return_value = 'счёт ООО Ромашка-Другое, ИНН 5001007304, товар x1'
        mock_scanned.return_value = False
        mock_llm.return_value = {
            'items': [{'name': 'Тестовый товар', 'quantity': 1.0, 'unit_price': 100.0}],
            'vendor': {'name': 'ООО Ромашка-Другое', 'tax_id': VALID_INN_A},
            'invoice_number': 'INV-TEST-1',
            'currency': '',
        }
        wizard = self._make_wizard()
        wizard.action_import()

        order = wizard.result_order_id
        self.assertTrue(order)
        self.assertEqual(order.partner_id, self.existing_partner)
        messages = order.message_ids.mapped('body')
        self.assertTrue(
            any(VALID_INN_A in body and 'Ромашка-Другое' in body for body in messages),
            'заказ должен получить примечание о расхождении названия при сопоставлении по ИНН')

    @patch('odoo.addons.purchase_pdf_import.wizard.pdf_import_wizard.call_llm')
    @patch('odoo.addons.purchase_pdf_import.wizard.pdf_import_wizard.pdf_is_scanned')
    @patch('odoo.addons.purchase_pdf_import.wizard.pdf_import_wizard.extract_text_from_pdf')
    def test_no_note_when_name_matches(self, mock_extract, mock_scanned, mock_llm):
        mock_extract.return_value = 'счёт ООО Ромашка, ИНН 5001007304, товар x1'
        mock_scanned.return_value = False
        mock_llm.return_value = {
            'items': [{'name': 'Тестовый товар', 'quantity': 1.0, 'unit_price': 100.0}],
            'vendor': {'name': 'ООО "Ромашка"', 'tax_id': VALID_INN_A},
            'invoice_number': 'INV-TEST-2',
            'currency': '',
        }
        wizard = self._make_wizard()
        wizard.action_import()

        order = wizard.result_order_id
        self.assertEqual(order.partner_id, self.existing_partner)
        messages = order.message_ids.mapped('body')
        self.assertFalse(
            any(VALID_INN_A in body for body in messages),
            'название совпадает - примечания о расхождении быть не должно')

    @patch('odoo.addons.purchase_pdf_import.wizard.pdf_import_wizard.call_llm')
    @patch('odoo.addons.purchase_pdf_import.wizard.pdf_import_wizard.pdf_is_scanned')
    @patch('odoo.addons.purchase_pdf_import.wizard.pdf_import_wizard.extract_text_from_pdf')
    def test_manual_vendor_id_still_skips_matching_entirely(self, mock_extract, mock_scanned, mock_llm):
        # Ручной выбор поставщика (self.vendor_id) обязан по-прежнему иметь
        # приоритет над любым автопоиском - строка "self.vendor_id or
        # self._find_or_create_vendor(...)" в базовом модуле не менялась.
        manual_partner = self.env['res.partner'].create({'name': 'Ручной Выбор Поставщика'})
        mock_extract.return_value = 'счёт ООО Ромашка-Другое, ИНН 5001007304, товар x1'
        mock_scanned.return_value = False
        mock_llm.return_value = {
            'items': [{'name': 'Тестовый товар', 'quantity': 1.0, 'unit_price': 100.0}],
            'vendor': {'name': 'ООО Ромашка-Другое', 'tax_id': VALID_INN_A},
            'invoice_number': 'INV-TEST-3',
            'currency': '',
        }
        wizard = self.env['purchase.pdf.import.wizard'].create({
            'pdf_file': base64.b64encode(b'%PDF-1.4 fake test content'),
            'payment_type': 'full_prepay',
            'vendor_id': manual_partner.id,
        })
        wizard.action_import()

        self.assertEqual(wizard.result_order_id.partner_id, manual_partner)
        messages = wizard.result_order_id.message_ids.mapped('body')
        self.assertFalse(any(VALID_INN_A in body for body in messages),
                          'при ручном выборе поставщика сопоставление по ИНН вообще не должно запускаться')


@tagged('post_install', '-at_install')
class TestVendorMatchingUpddPickingChatter(TransactionCase):
    """_find_or_create_vendor в УПД-мастере используется только внутри
    _confirm_without_order (нет заказа вообще) - примечание в этом случае
    должно попасть в чат приёмки, не заказа."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.existing_partner = cls.env['res.partner'].create({
            'name': 'ООО "Ромашка"', 'vat': VALID_INN_A, 'supplier_rank': 1,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Тестовый товар для УПД', 'is_storable': True, 'purchase_ok': True,
        })

    def test_picking_gets_inn_match_note_without_order(self):
        wizard = self.env['purchase.updd.import.wizard'].create({
            'pdf_file': base64.b64encode(b'%PDF-1.4 fake updd content'),
            'recognized_seller_name': 'ООО Ромашка-Другое',
            'recognized_seller_inn': VALID_INN_A,
            'recognized_amount': 100.0,
            'recognized_number': 'УПД-ТЕСТ-1',
            'line_ids': [(0, 0, {'product_id': self.product.id, 'quantity': 1.0})],
        })
        wizard._confirm_without_order()

        picking = self.env['stock.picking'].search(
            [('partner_id', '=', self.existing_partner.id)], order='id desc', limit=1)
        self.assertTrue(picking)
        messages = picking.message_ids.mapped('body')
        self.assertTrue(
            any(VALID_INN_A in body and 'Ромашка-Другое' in body for body in messages),
            'приёмка без заказа должна получить примечание о расхождении названия')
