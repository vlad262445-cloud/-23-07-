from odoo import api, fields, models


class StockLocation(models.Model):
    _inherit = 'stock.location'

    warehouse_quant_count = fields.Integer(
        string='Товаров с остатком', compute='_compute_warehouse_quant_stats',
        help='Сколько разных товаров с ненулевым остатком лежит в этой '
             'локации и во всех вложенных ячейках/полках/органайзерах.')
    warehouse_total_quantity = fields.Float(
        string='Общее количество', compute='_compute_warehouse_quant_stats',
        help='Суммарное количество (по всем товарам) в этой локации и во '
             'всех вложенных ячейках/полках/органайзерах.')
    warehouse_description = fields.Text(
        string='Описание',
        help='Что здесь хранится - для стеллажей/полок/органайзеров, чтобы '
             'было легче искать товар.')

    def _compute_warehouse_quant_stats(self):
        for location in self:
            quants = self.env['stock.quant'].search([
                ('location_id', 'child_of', location.id),
                ('quantity', '!=', 0),
            ])
            location.warehouse_quant_count = len(quants.product_id)
            location.warehouse_total_quantity = sum(quants.mapped('quantity'))

    def action_warehouse_drill_down(self):
        self.ensure_one()
        if self.child_ids:
            return self._warehouse_kanban_action(self.id, self.display_name)
        return {
            'type': 'ir.actions.act_window',
            'name': self.display_name,
            'res_model': 'stock.quant',
            'view_mode': 'list',
            'domain': [('location_id', '=', self.id)],
        }

    @api.model
    def _warehouse_kanban_action(self, parent_id, name):
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': 'stock.location',
            'view_mode': 'kanban',
            'views': [(self.env.ref(
                'purchase_pdf_import.view_stock_location_kanban_warehouse').id, 'kanban')],
            'domain': [('location_id', '=', parent_id)],
            'context': {'default_location_id': parent_id},
        }
