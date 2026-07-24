from odoo import fields, models


class PurchaseRequestDepartment(models.Model):
    _inherit = 'purchase.request.department'

    # Сопоставление со справочником Б (hr.department, "по участкам") - п.
    # 10.2 ТЗ, решение "два справочника + сопоставление". Заполняется
    # заказчиком вручную после установки, не угадывается кодом - пустое
    # значение здесь нормальное состояние.
    hr_department_id = fields.Many2one('hr.department', string='Подразделение (спецодежда)')
