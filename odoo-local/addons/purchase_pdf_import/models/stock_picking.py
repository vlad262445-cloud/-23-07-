from odoo import api, fields, models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    source_purchase_order_id = fields.Many2one(
        'purchase.order', string='Заказ на закупку',
        compute='_compute_source_purchase_order_id', store=True)

    @api.depends('group_id')
    def _compute_source_purchase_order_id(self):
        for picking in self:
            order = self.env['purchase.order']
            if picking.group_id:
                order = self.env['purchase.order'].search(
                    [('group_id', '=', picking.group_id.id)], limit=1)
            picking.source_purchase_order_id = order

    def action_view_source_purchase_order(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'res_id': self.source_purchase_order_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # УПД подтверждает именно ЭТУ приёмку - удобнее загрузить/пропустить его
    # сразу здесь, а не заставлять человека возвращаться на заказ, с которого
    # он попал сюда через "Получать продукты".
    updd_line_ids = fields.One2many(
        'purchase.updd.line', compute='_compute_updd_line_ids', string='УПД')
    updd_skipped = fields.Boolean(related='source_purchase_order_id.updd_skipped', string='УПД пропущен')

    @api.depends('source_purchase_order_id.updd_line_ids')
    def _compute_updd_line_ids(self):
        # Обычно УПД привязан к заказу (order.updd_line_ids), но мастер
        # импорта УПД умеет создавать приёмку и без заказа вообще - тогда
        # запись покажет picking_id вместо purchase_order_id.
        for picking in self:
            if picking.source_purchase_order_id:
                picking.updd_line_ids = picking.source_purchase_order_id.updd_line_ids
            else:
                picking.updd_line_ids = self.env['purchase.updd.line'].search(
                    [('picking_id', '=', picking.id)])

    def action_open_updd_import_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.updd.import.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_purchase_order_id': self.source_purchase_order_id.id},
        }

    def action_skip_updd(self):
        for picking in self:
            picking.source_purchase_order_id.action_skip_updd()

    def _action_done(self):
        res = super()._action_done()
        self._sync_request_state_from_picking()
        return res

    def _sync_request_state_from_picking(self):
        # Статусы "В пути"/"На складе" заявки никак не были связаны со
        # складской приёмкой - как только приёмка реально проведена
        # ("Готово"), считаем товар полученным и продвигаем заявку.
        for picking in self:
            if picking.state != 'done' or picking.picking_type_id.code != 'incoming':
                continue
            order = picking.source_purchase_order_id
            if not order:
                continue
            requests = order.request_ids.filtered(lambda r: r.state != 'in_stock')
            requests.write({'state': 'in_stock'})
            # Если платёжка по "оплате после получения"/финальному платежу
            # 50/50 была загружена ДО того, как приёмка подтверждена,
            # _payment_target_state придержал продвижение статуса до этого
            # момента (см. purchase_order.py) - теперь, когда приёмка
            # наконец проведена, нужно довести уже готовый платёж до конца.
            target_state = order._payment_target_state()
            if target_state:
                order._advance_request_state(order.request_ids, target_state)
