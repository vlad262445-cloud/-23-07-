from odoo import _, fields, models

from .inn_utils import is_valid_inn, normalize_inn


class VendorMatchingMixin(models.AbstractModel):
    """Общая логика поиска поставщика по ИНН для обоих мастеров ИИ-импорта
    (PDF-счёт и УПД). Сигнатуры их _find_or_create_vendor разные (первый
    принимает dict от ИИ, второй - name/vat по отдельности), поэтому каждый
    мастер сам приводит свои данные к паре (имя, ИНН) и вызывает
    _match_vendor_by_inn - здесь только эта общая часть."""

    _name = 'purchase.vendor.matching.mixin'
    _description = 'Сопоставление поставщика по ИНН (общая логика для мастеров импорта)'

    # store=False, без compute - служебное поле-черновик, живёт только в
    # кеше записи на время текущего вызова. Нужно передать примечание из
    # _find_or_create_vendor (вызывается изнутри базового action_import /
    # _confirm_without_order) туда, где уже известен получившийся заказ или
    # приёмка - обычные python-атрибуты на recordset поставить нельзя,
    # у моделей Odoo __slots__.
    vendor_match_note = fields.Char(store=False)

    def _match_vendor_by_inn(self, raw_inn, invoice_name):
        """Ищет поставщика по нормализованному ИНН.

        Возвращает (partner, note):
        - partner - res.partner (пустой recordset, если по ИНН не найден
          однозначно - тогда вызывающий откатывается на поиск по имени);
        - note - текст для чаттера, если название в счёте отличается от
          названия в карточке, иначе None.

        Намеренно не пытается сопоставлять по паре ИНН+КПП: схема данных,
        которую отдаёт ИИ-экстракция (VENDOR_SCHEMA в pdf_import_wizard.py),
        КПП не содержит вообще. Если по ИНН находится больше одной карточки
        (разные филиалы одного юрлица с разными КПП - см. п. 9.2 ТЗ), без
        КПП из документа различить их нечем, и метод намеренно не выбирает
        наугад, а даёт вызывающему откатиться на поиск по имени - тогда либо
        сработает точное совпадение имени, либо создастся новая карточка, но
        мы точно не привяжем заказ не к тому филиалу. Подробнее - NOTES.md.
        """
        inn = normalize_inn(raw_inn)
        if not inn or not is_valid_inn(inn):
            return self.env['res.partner'], None

        candidates = self.env['res.partner'].search([('vat', '=', inn)])
        if len(candidates) != 1:
            return self.env['res.partner'], None

        partner = candidates
        note = None
        invoice_name = (invoice_name or '').strip()
        card_name = (partner.name or '').strip()
        if invoice_name and invoice_name.casefold() != card_name.casefold():
            note = _(
                'Поставщик определён по ИНН %(inn)s. В счёте указано '
                'наименование «%(invoice_name)s», в карточке — '
                '«%(card_name)s». Проверьте, если это ошибка.'
            ) % {'inn': inn, 'invoice_name': invoice_name, 'card_name': card_name}
        return partner, note
