from odoo import fields, models


class PurchaseDeliveryTrackingWizard(models.TransientModel):
    _name = 'purchase.delivery.tracking.wizard'
    _description = 'Оформление доставки'

    purchase_order_id = fields.Many2one('purchase.order', required=True)
    delivery_method_id = fields.Many2one(
        'purchase.delivery.method', string='Способ доставки', required=True)
    delivery_method_has_tracking = fields.Boolean(related='delivery_method_id.has_tracking')
    tracking_number = fields.Char(string='Трек-номер / примечание')
    shipped_date = fields.Date(string='Дата отправки', default=fields.Date.context_today)

    def action_confirm(self):
        self.ensure_one()
        self.purchase_order_id.write({
            'delivery_method_id': self.delivery_method_id.id,
            'tracking_number': self.tracking_number,
            'shipped_date': self.shipped_date,
        })
        # Логику перехода статуса не дублируем и не переписываем - вызываем
        # существующий метод как есть (см. п. 5.2 ТЗ).
        self.purchase_order_id.action_arrange_delivery()
        return {'type': 'ir.actions.act_window_close'}
