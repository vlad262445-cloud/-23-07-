from odoo import _, fields, models


class WarehouseRackWizard(models.TransientModel):
    _name = 'warehouse.rack.wizard'
    _description = 'Добавить стеллаж'

    parent_location_id = fields.Many2one(
        'stock.location', string='Родительская локация', required=True,
        domain=[('usage', '=', 'view')])
    name = fields.Char(string='Название стеллажа', required=True)
    shelf_count = fields.Integer(string='Количество полок', required=True, default=1)
    has_organizers = fields.Boolean(
        string='С органайзерами/ячейками',
        help='Включите для мелкого инструмента, которое раскладывается по '
             'органайзерам с ячейками. Выключите для крупной оснастки, '
             'которая хранится прямо на полке.')

    def action_confirm(self):
        self.ensure_one()
        rack = self.env['stock.location'].create({
            'name': self.name,
            'location_id': self.parent_location_id.id,
            'usage': 'view',
        })
        shelf_usage = 'view' if self.has_organizers else 'internal'
        for i in range(1, self.shelf_count + 1):
            self.env['stock.location'].create({
                'name': _('Полка %s') % i,
                'location_id': rack.id,
                'usage': shelf_usage,
            })
        return rack.action_warehouse_drill_down()
