from odoo import fields, models


class HrEmployeeWorkwearSize(models.Model):
    _name = 'hr.employee.workwear.size'
    _description = 'Размер спецодежды сотрудника'
    _order = 'type_id'

    employee_id = fields.Many2one('hr.employee', required=True, ondelete='cascade')
    type_id = fields.Many2one('hr.workwear.type', string='Тип', required=True)
    # Только для домена size_id во вьюхе (п. 10.4 ТЗ - "для типа со шкалой
    # shoe не предлагаются S/M/L") - вспомогательное нехранимое поле, само
    # по себе никогда не читается кодом.
    type_size_scale = fields.Selection(related='type_id.size_scale')
    size_id = fields.Many2one('hr.workwear.size', string='Размер')
    note = fields.Char()

    _sql_constraints = [
        ('employee_type_unique', 'unique(employee_id, type_id)',
         'У сотрудника уже есть размер для этого типа спецодежды.'),
    ]
