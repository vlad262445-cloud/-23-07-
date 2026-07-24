from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class HrWorkwearNorm(models.Model):
    _name = 'hr.workwear.norm'
    _description = 'Норма выдачи спецодежды'

    job_id = fields.Many2one('hr.job', string='Должность')
    department_id = fields.Many2one('hr.department', string='Подразделение')
    type_id = fields.Many2one('hr.workwear.type', string='Тип', required=True)
    quantity = fields.Integer(string='Количество', default=1)
    period_months = fields.Integer(string='Период, мес.', required=True)

    @api.constrains('job_id', 'department_id')
    def _check_job_or_department(self):
        for norm in self:
            if bool(norm.job_id) == bool(norm.department_id):
                raise ValidationError(_(
                    'Норма выдачи должна быть привязана либо к должности, '
                    'либо к подразделению (ровно к одному из двух).'))

    @api.model_create_multi
    def create(self, vals_list):
        # @api.constrains('job_id', 'department_id') не срабатывает сам,
        # если ни одно из этих полей вообще не передано в vals (Odoo
        # запускает проверку только для полей, реально затронутых
        # create/write, а "оба отсутствуют" не трогает ни одно из них) -
        # найдено тестом test_norm_requires_job_or_department_not_both.
        # Явный вызов ниже гарантирует проверку в любом случае.
        norms = super().create(vals_list)
        norms._check_job_or_department()
        return norms
