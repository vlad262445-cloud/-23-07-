from odoo import _, api, fields, models
from odoo.exceptions import UserError


class WarehouseScrapRequest(models.Model):
    _name = 'warehouse.scrap.request'
    _description = 'Заявка на списание'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(string='Номер', default=lambda self: _('Новая'), copy=False, readonly=True)
    requester_id = fields.Many2one(
        'res.users', string='Заявитель', required=True, tracking=True,
        default=lambda self: self.env.user)
    source_location_id = fields.Many2one(
        'stock.location', string='Склад/участок', required=True,
        domain=[('usage', '=', 'internal')], tracking=True)
    product_id = fields.Many2one('product.product', string='Товар', required=True)
    available_quantity = fields.Float(
        string='Остаток сейчас', compute='_compute_available_quantity',
        help='Справочно: сколько товара сейчас числится в выбранной локации. '
             'Пока локация не выбрана — остаток по всем локациям сразу.')
    quantity = fields.Float(string='Количество', required=True)
    reason = fields.Text(string='Причина', required=True)
    state = fields.Selection([
        ('to_approve', 'Ожидает решения'),
        ('approved', 'Одобрено'),
        ('refused', 'Отклонено'),
    ], string='Статус', default='to_approve', required=True, tracking=True, copy=False)
    refuse_reason = fields.Text(string='Причина отклонения')
    scrap_id = fields.Many2one('stock.scrap', string='Списание', readonly=True)

    @api.depends('product_id', 'source_location_id')
    def _compute_available_quantity(self):
        for request in self:
            if not request.product_id:
                request.available_quantity = 0.0
            elif request.source_location_id:
                request.available_quantity = request.product_id.with_context(
                    location=request.source_location_id.id).qty_available
            else:
                request.available_quantity = request.product_id.qty_available

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals.get('name') == _('Новая'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'warehouse.scrap.request') or _('Новая')
        requests = super().create(vals_list)
        for request in requests:
            request._notify_decision_makers()
        return requests

    def _notify_decision_makers(self):
        self.ensure_one()
        buyers = self.env.ref('purchase.group_purchase_user').users
        for buyer in buyers:
            self.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_('Требуется решение по заявке на списание'),
                note=_('Заявка %s ожидает вашего решения.') % self.name,
                user_id=buyer.id,
            )

    def action_approve(self):
        self.ensure_one()
        if self.state != 'to_approve':
            raise UserError(_('Решение по этой заявке уже принято.'))
        scrap = self.env['stock.scrap'].create({
            'product_id': self.product_id.id,
            'scrap_qty': self.quantity,
            'location_id': self.source_location_id.id,
            'origin': self.name,
        })
        scrap.action_validate()
        self.write({'state': 'approved', 'scrap_id': scrap.id})
        self.activity_ids.unlink()
        self.message_post(body=_('Заявка на списание одобрена.'))

    def action_refuse(self):
        self.ensure_one()
        if self.state != 'to_approve':
            raise UserError(_('Решение по этой заявке уже принято.'))
        if not self.refuse_reason:
            raise UserError(_('Укажите причину отклонения.'))
        self.write({'state': 'refused'})
        self.activity_ids.unlink()
        self.message_post(body=_('Заявка на списание отклонена: %s') % self.refuse_reason)
