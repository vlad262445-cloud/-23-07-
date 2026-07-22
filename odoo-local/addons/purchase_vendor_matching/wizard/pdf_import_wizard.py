from odoo import models

from .inn_utils import normalize_inn


class PdfImportWizard(models.TransientModel):
    _name = 'purchase.pdf.import.wizard'
    _inherit = ['purchase.pdf.import.wizard', 'purchase.vendor.matching.mixin']

    def _partner_vals_from_ai(self, vendor_data):
        # Нормализация ИНН при записи - см. п. 9.2 ТЗ: как ИИ прочитал
        # ("7710 01001", "ИНН 7710010019"), так раньше и попадало в vat,
        # и следующее сопоставление по нему уже не срабатывало. Это
        # единственное место, где _partner_vals_from_ai заполняет 'vat' -
        # действует и когда карточка находится по имени и дозаполняется,
        # и когда создаётся новая (оба пути в базовом методе вызывают
        # именно этот метод).
        vals = super()._partner_vals_from_ai(vendor_data)
        if vals.get('vat'):
            vals['vat'] = normalize_inn(vals['vat'])
        return vals

    def _find_or_create_vendor(self, vendor_data):
        name = (vendor_data.get('name') or '').strip()
        if not name:
            return False

        partner, note = self._match_vendor_by_inn(vendor_data.get('tax_id'), name)
        self.vendor_match_note = note
        if not partner:
            # ИНН не помог (не пришёл, не прошёл контрольную сумму, не
            # нашёлся однозначно) - откатываемся на оригинальный порядок:
            # поиск по имени, затем создание новой карточки. Это ровно
            # то же поведение, что было до этого модуля.
            return super()._find_or_create_vendor(vendor_data)

        # Найден по ИНН - дозаполняем только пустые поля, как и раньше;
        # повторный поиск по имени не нужен, он бы только продублировал
        # то же самое решение.
        vals = self._partner_vals_from_ai(vendor_data)
        if not partner.supplier_rank:
            vals['supplier_rank'] = 1
        vals = {k: v for k, v in vals.items() if v and not partner[k]}
        if vals:
            # sudo() - см. пояснение у оригинального метода: дозаполнение
            # реквизитов поставщика из счёта - штатная часть импорта, а не
            # то, что должно требовать отдельного права на запись в контакты
            # у каждой роли, которой разрешено оформлять заказ.
            partner.sudo().write(vals)
        return partner

    def action_import(self):
        self.vendor_match_note = False
        result = super().action_import()
        if self.vendor_match_note and self.result_order_id:
            self.result_order_id.message_post(body=self.vendor_match_note)
        return result
