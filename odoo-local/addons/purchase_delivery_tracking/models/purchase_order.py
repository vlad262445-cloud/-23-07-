from odoo import _, api, fields, models

# Ссылка строится только для способов доставки, у которых трек-код
# действительно устроен как код (не свободный текст) - см. п. 5.3 ТЗ.
# Ключ - точное название способа доставки из data/purchase_delivery_method_data.xml.
TRACKING_URL_TEMPLATES = {
    'СДЭК': 'https://www.cdek.ru/ru/tracking?order_id=%s',
    'Почта России': 'https://www.pochta.ru/tracking#%s',
    'Деловые Линии': 'https://www.dellin.ru/requests/?number=%s',
    'ПЭК': 'https://pecom.ru/ru/tracking/?number=%s',
}


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    delivery_method_id = fields.Many2one(
        'purchase.delivery.method', string='Способ доставки', tracking=True)
    tracking_number = fields.Char(
        string='Трек-номер / примечание', tracking=True, copy=False,
        help='Свободный текст - код транспортной компании или пояснение '
             '("водитель поставщика, приедет в четверг"). Не обязателен.')
    shipped_date = fields.Date(string='Дата отправки')
    tracking_url = fields.Char(compute='_compute_tracking_url')
    delivery_summary = fields.Char(compute='_compute_delivery_summary', string='Доставка')

    @api.depends('delivery_method_id', 'tracking_number')
    def _compute_tracking_url(self):
        for order in self:
            template = TRACKING_URL_TEMPLATES.get(order.delivery_method_id.name)
            number = (order.tracking_number or '').strip()
            # Пробел в номере - явный признак свободного текста ("водитель
            # поставщика"), а не кода: для него ссылку не строим.
            if template and number and ' ' not in number:
                order.tracking_url = template % number
            else:
                order.tracking_url = False

    @api.depends('delivery_method_id', 'tracking_number')
    def _compute_delivery_summary(self):
        for order in self:
            if not order.delivery_method_id:
                order.delivery_summary = False
            elif order.tracking_number:
                order.delivery_summary = f"{order.delivery_method_id.name} · {order.tracking_number}"
            else:
                order.delivery_summary = order.delivery_method_id.name

    def action_open_delivery_tracking_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.delivery.tracking.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_purchase_order_id': self.id},
        }

    def _delivery_arranged_message(self):
        self.ensure_one()
        if not self.delivery_method_id:
            return False
        if self.tracking_number:
            return _('Доставка оформлена — %(method)s, трек-номер %(number)s.') % {
                'method': self.delivery_method_id.name, 'number': self.tracking_number,
            }
        return _('Доставка оформлена — %s.') % self.delivery_method_id.name

    def action_arrange_delivery(self):
        result = super().action_arrange_delivery()
        for order in self:
            message = order._delivery_arranged_message()
            if not message:
                continue
            order.message_post(body=message)
            # См. пояснение в базовом методе - заявитель не имеет доступа к
            # заказу и видит только свою заявку.
            for request in order.request_ids:
                request.message_post(body=message)
        return result
