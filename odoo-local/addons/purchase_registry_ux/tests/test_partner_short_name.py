from odoo.tests.common import BaseCase, TransactionCase, tagged

from odoo.addons.purchase_registry_ux.models.res_partner import build_short_name


@tagged('post_install', '-at_install')
class TestBuildShortNameFunction(BaseCase):
    """Чистая функция - реальные названия из боевой базы (см.
    work/analytics_reference.md и вывод SELECT name, vat FROM res_partner)."""

    def test_ooo_quoted_name_kept_as_is(self):
        self.assertEqual(build_short_name('ООО "СИЭНСИЭМ Груп"'), 'ООО "СИЭНСИЭМ Груп"')
        self.assertEqual(build_short_name('ООО "Дата-В"'), 'ООО "Дата-В"')
        self.assertEqual(build_short_name('ООО «ОТМ-М»'), 'ООО «ОТМ-М»')

    def test_ooo_suffix_form(self):
        self.assertEqual(build_short_name('Интернет Решения, ООО'), 'ООО Интернет Решения')

    def test_ooo_full_legal_form_normalized_to_abbreviation(self):
        self.assertEqual(
            build_short_name('Общество с ограниченной ответственностью "Ветки"'),
            'ООО "Ветки"')

    def test_ip_with_fio_shortened(self):
        self.assertEqual(build_short_name('ИП Асадуллин Владислав Олегович'), 'Асадуллин В.О.')
        self.assertEqual(
            build_short_name('Индивидуальный предприниматель Каюмова Наталья Михайловна'),
            'Каюмова Н.М.')

    def test_empty_and_none(self):
        self.assertEqual(build_short_name(''), '')
        self.assertEqual(build_short_name(None), '')

    def test_name_without_known_legal_form_unchanged(self):
        self.assertEqual(build_short_name('Просто Название Без Формы'), 'Просто Название Без Формы')


@tagged('post_install', '-at_install')
class TestPartnerShortNameField(TransactionCase):

    def test_auto_filled_on_create(self):
        partner = self.env['res.partner'].create({'name': 'ООО "Тестовый Партнёр"'})
        self.assertEqual(partner.short_name, 'ООО "Тестовый Партнёр"')
        self.assertFalse(partner.short_name_manual)

    def test_manual_override_not_overwritten_on_name_change(self):
        partner = self.env['res.partner'].create({'name': 'ООО "Исходное Имя"'})
        partner.short_name = 'Моё Ручное Имя'
        self.assertTrue(partner.short_name_manual)

        partner.name = 'ООО "Совсем Другое Имя"'
        self.assertEqual(
            partner.short_name, 'Моё Ручное Имя',
            'ручная правка не должна перетираться автопересчётом при смене полного имени')

    def test_name_change_updates_short_name_when_not_manual(self):
        partner = self.env['res.partner'].create({'name': 'ООО "Старое Имя"'})
        self.assertEqual(partner.short_name, 'ООО "Старое Имя"')

        partner.name = 'ООО "Новое Имя"'
        self.assertEqual(partner.short_name, 'ООО "Новое Имя"')
