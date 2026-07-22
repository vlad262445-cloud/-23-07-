from odoo import fields, models


class PurchaseRequest(models.Model):
    _inherit = 'purchase.request'

    # readonly (related без readonly=False) - заявителю важно видеть, как
    # едет его товар, но не менять это здесь (см. п. 5.3 ТЗ).
    delivery_method_id = fields.Many2one(
        related='purchase_order_id.delivery_method_id', string='Способ доставки')
    tracking_number = fields.Char(
        related='purchase_order_id.tracking_number', string='Трек-номер / примечание')
    shipped_date = fields.Date(
        related='purchase_order_id.shipped_date', string='Дата отправки')

    def action_open_delivery_tracking_wizard(self):
        self.ensure_one()
        return self.purchase_order_id.action_open_delivery_tracking_wizard()
