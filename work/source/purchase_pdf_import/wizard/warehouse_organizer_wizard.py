from odoo import _, fields, models


class WarehouseOrganizerWizard(models.TransientModel):
    _name = 'warehouse.organizer.wizard'
    _description = 'Добавить органайзер'

    shelf_location_id = fields.Many2one(
        'stock.location', string='Полка', required=True,
        domain=[('usage', '=', 'view')])
    name = fields.Char(string='Название органайзера', required=True)
    cell_count = fields.Integer(string='Количество ячеек', required=True, default=1)

    def action_confirm(self):
        self.ensure_one()
        organizer = self.env['stock.location'].create({
            'name': self.name,
            'location_id': self.shelf_location_id.id,
            'usage': 'view',
        })
        for i in range(1, self.cell_count + 1):
            self.env['stock.location'].create({
                'name': _('Ячейка %s') % i,
                'location_id': organizer.id,
                'usage': 'internal',
            })
        return organizer.action_warehouse_drill_down()
