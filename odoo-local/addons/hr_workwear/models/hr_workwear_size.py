from odoo import fields, models


class HrWorkwearSize(models.Model):
    _name = 'hr.workwear.size'
    _description = 'Значение размера спецодежды'
    _order = 'scale, sequence'

    name = fields.Char(required=True)
    scale = fields.Selection([
        ('alpha', 'Буквенный (S/M/L/XL)'),
        ('numeric', 'Числовой (44-64)'),
        ('shoe', 'Обувной (36-48)'),
        ('head', 'Головной убор (54-62)'),
        ('none', 'Безразмерное'),
    ], required=True)
    sequence = fields.Integer(default=10)
