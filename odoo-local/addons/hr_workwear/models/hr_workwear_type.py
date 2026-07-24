from odoo import fields, models


class HrWorkwearType(models.Model):
    _name = 'hr.workwear.type'
    _description = 'Тип СИЗ/спецодежды'
    _order = 'name'

    name = fields.Char(required=True)
    size_scale = fields.Selection([
        ('alpha', 'Буквенный (S/M/L/XL)'),
        ('numeric', 'Числовой (44-64)'),
        ('shoe', 'Обувной (36-48)'),
        ('head', 'Головной убор (54-62)'),
        ('none', 'Безразмерное'),
    ], required=True, default='alpha')
    product_id = fields.Many2one('product.product', string='Номенклатура для списания')
    wear_period_months = fields.Integer(string='Типовой срок носки, мес.', default=12)
    active = fields.Boolean(default=True)
