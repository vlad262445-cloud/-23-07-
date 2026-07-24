from dateutil.relativedelta import relativedelta

from odoo import api, fields, models


class HrWorkwearRequirementLine(models.Model):
    """Экран "Потребность" (п. 10.7/10.8 ТЗ) - тип СИЗ x размер, сколько
    выдать в ближайшие 3 месяца по нормам. Обычная (не SQL-view) модель,
    перестраиваемая методом _refresh() поверх hr.employee.
    _get_workwear_norm_due_lines() - того же метода, что считает
    просрочку на карточке сотрудника, чтобы не завести вторую, SQL-версию
    той же арифметики дат и не разойтись с ней молча."""
    _name = 'hr.workwear.requirement.line'
    _description = 'Потребность в спецодежде'
    _order = 'due_date'

    employee_id = fields.Many2one('hr.employee', required=True, ondelete='cascade')
    department_id = fields.Many2one(related='employee_id.department_id', store=True)
    type_id = fields.Many2one('hr.workwear.type', required=True)
    size_id = fields.Many2one('hr.workwear.size')
    quantity = fields.Integer(default=1)
    due_date = fields.Date(required=True)
    is_overdue = fields.Boolean(compute='_compute_is_overdue')

    @api.depends('due_date')
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for line in self:
            line.is_overdue = bool(line.due_date and line.due_date <= today)

    @api.model
    def _refresh(self, horizon_months=3):
        self.sudo().search([]).unlink()
        employees = self.env['hr.employee'].search([('workwear_not_required', '=', False)])
        if not employees:
            return
        horizon = fields.Date.context_today(self) + relativedelta(months=horizon_months)
        vals_list = []
        for row in employees._get_workwear_norm_due_lines():
            if row['due_date'] > horizon:
                continue
            emp = row['employee']
            norm = row['norm']
            size = emp.workwear_size_ids.filtered(lambda s: s.type_id == norm.type_id)[:1].size_id
            vals_list.append({
                'employee_id': emp.id,
                'type_id': norm.type_id.id,
                'size_id': size.id if size else False,
                'quantity': norm.quantity,
                'due_date': row['due_date'],
            })
        if vals_list:
            self.sudo().create(vals_list)

    def action_create_purchase_request(self):
        """Кнопка "Создать заявку на закупку" (п. 10.8 ТЗ) - строки
        purchase.request.line текстовые, без привязки к product.product
        (так задумано в базовом модуле, см. его CHANGELOG от 2026-07-07 -
        не ломаем). Дальше заявка идёт обычным маршрутом, этот метод
        только создаёт её."""
        if not self:
            return False
        line_vals = []
        for req in self:
            size_label = (', размер %s' % req.size_id.name) if req.size_id else ''
            line_vals.append((0, 0, {
                'name': '%s%s' % (req.type_id.name, size_label),
                'product_qty': req.quantity,
                'uom_label': 'шт',
            }))
        warehouse_department = self.env['purchase.request.department'].search(
            [('name', '=', 'Склад')], limit=1)
        request = self.env['purchase.request'].create({
            'requested_by': self.env.user.id,
            'department_id': warehouse_department.id if warehouse_department else False,
            'line_ids': line_vals,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.request',
            'res_id': request.id,
            'view_mode': 'form',
            'target': 'current',
        }
