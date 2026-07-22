from odoo import models

from .inn_utils import normalize_inn


class UpddImportWizard(models.TransientModel):
    _name = 'purchase.updd.import.wizard'
    _inherit = ['purchase.updd.import.wizard', 'purchase.vendor.matching.mixin']

    def _find_or_create_vendor(self, name, vat):
        name = (name or '').strip()
        if not name:
            return self.env['res.partner']

        # Нормализуем сразу - именно это значение пойдёт и в дозаполнение
        # найденной карточки ниже, и (через super()) в создание новой, если
        # ИНН не помог. Второе поле передачи нормализованного vat в базовый
        # метод избавляет от необходимости переопределять его целиком ради
        # одной строчки записи 'vat'.
        vat_normalized = normalize_inn(vat)

        partner, note = self._match_vendor_by_inn(vat_normalized, name)
        self.vendor_match_note = note
        if not partner:
            return super()._find_or_create_vendor(name, vat_normalized or False)

        vals = {}
        if not partner.supplier_rank:
            vals['supplier_rank'] = 1
        if vat_normalized and not partner.vat:
            vals['vat'] = vat_normalized
        if vals:
            partner.sudo().write(vals)
        return partner

    def _create_standalone_picking(self, partner):
        # Единственное место, где мастер УПД без заказа что-то создаёт из
        # найденного/созданного поставщика - подходящая точка, чтобы
        # прикрепить примечание о сопоставлении по ИНН к чаттеру (заказа
        # здесь нет вообще, см. _confirm_without_order в базовом модуле).
        picking = super()._create_standalone_picking(partner)
        if self.vendor_match_note:
            picking.sudo().message_post(body=self.vendor_match_note)
        return picking
