from odoo import fields, models


class PurchasePaymentLine(models.Model):
    _name = 'purchase.payment.line'
    _description = 'Платёж по закупке'
    _order = 'id desc'

    purchase_order_id = fields.Many2one(
        'purchase.order', string='Закупка', required=True, ondelete='cascade')
    payment_date = fields.Char(string='Дата платежа (по документу)')
    payment_number = fields.Char(string='Номер платёжки')
    amount = fields.Float(string='Сумма')
    recipient_name = fields.Char(string='Получатель')
    recipient_inn = fields.Char(string='ИНН получателя (по документу)')
    purpose = fields.Char(string='Назначение платежа')
    partner_matched = fields.Boolean(string='Сверено с контрагентом')
    attachment_id = fields.Many2one('ir.attachment', string='Платёжное поручение')
