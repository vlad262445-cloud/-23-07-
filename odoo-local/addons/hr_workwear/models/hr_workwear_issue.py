from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models


class HrWorkwearIssue(models.Model):
    _name = 'hr.workwear.issue'
    _description = 'Выдача спецодежды'
    _inherit = ['mail.thread']
    _order = 'issue_date desc, id desc'

    name = fields.Char(default=lambda self: _('Новая'), copy=False, readonly=True)
    employee_id = fields.Many2one('hr.employee', string='Сотрудник', required=True, tracking=True)
    department_id = fields.Many2one(related='employee_id.department_id', store=True, string='Отдел')
    issue_date = fields.Date(string='Дата выдачи', required=True, default=fields.Date.context_today)
    state = fields.Selection([
        ('draft', 'Черновик'),
        ('issued', 'Выдано'),
        ('partially_returned', 'Частично возвращено'),
        ('returned', 'Возвращено'),
    ], default='draft', required=True, tracking=True)
    line_ids = fields.One2many('hr.workwear.issue.line', 'issue_id', string='Позиции')
    picking_id = fields.Many2one('stock.picking', string='Списание со склада', readonly=True, copy=False)
    issued_by = fields.Many2one('res.users', string='Кто выдал', default=lambda self: self.env.user)
    note = fields.Text(string='Комментарий')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals.get('name') == _('Новая'):
                vals['name'] = self.env['ir.sequence'].next_by_code('hr.workwear.issue') or _('Новая')
        return super().create(vals_list)

    def action_issue(self):
        self.write({'state': 'issued'})

    def _sync_return_state(self):
        """Состояние документа выводится из состояния его строк (п. 10.5
        ТЗ): всё возвращено -> returned, часть -> partially_returned."""
        for issue in self:
            if issue.state not in ('issued', 'partially_returned', 'returned'):
                continue
            if not issue.line_ids:
                continue
            returned = issue.line_ids.filtered('is_returned')
            if len(returned) == len(issue.line_ids):
                issue.state = 'returned'
            elif returned:
                issue.state = 'partially_returned'
            else:
                issue.state = 'issued'

    def action_create_picking(self):
        """Списание со склада (п. 10.5 ТЗ) - обычный stock.picking типа
        "Внутренние переводы" (тип ищется по коду, а не по угаданному
        xmlid - id может отличаться между базами), по кнопке, не
        автоматически: на старте эксплуатации остатков может не быть, и
        жёсткая привязка заблокировала бы выдачу."""
        for issue in self:
            if issue.picking_id:
                continue
            picking_type = self.env['stock.picking.type'].search([('code', '=', 'internal')], limit=1)
            if not picking_type:
                continue
            moves = []
            for line in issue.line_ids:
                if not line.product_id:
                    continue
                moves.append((0, 0, {
                    'name': line.product_id.name,
                    'product_id': line.product_id.id,
                    'product_uom_qty': line.quantity,
                    'product_uom': line.product_id.uom_id.id,
                    'location_id': picking_type.default_location_src_id.id,
                    'location_dest_id': picking_type.default_location_dest_id.id,
                }))
            if not moves:
                continue
            picking = self.env['stock.picking'].create({
                'picking_type_id': picking_type.id,
                'location_id': picking_type.default_location_src_id.id,
                'location_dest_id': picking_type.default_location_dest_id.id,
                'origin': issue.name,
                'move_ids': moves,
            })
            picking.action_confirm()
            issue.picking_id = picking.id


class HrWorkwearIssueLine(models.Model):
    _name = 'hr.workwear.issue.line'
    _description = 'Строка выдачи спецодежды'

    issue_id = fields.Many2one('hr.workwear.issue', required=True, ondelete='cascade')
    # related+store: удобно для батч-агрегации в hr.employee._get_workwear_due_stats
    # (read_group по employee_id/issue_date без обхода через issue_id) - тот
    # же приём, что уже применялся в проекте для отчётных полей.
    employee_id = fields.Many2one(related='issue_id.employee_id', store=True, string='Сотрудник')
    issue_date = fields.Date(related='issue_id.issue_date', store=True, string='Дата выдачи')

    type_id = fields.Many2one('hr.workwear.type', string='Тип', required=True)
    type_size_scale = fields.Selection(related='type_id.size_scale')
    size_id = fields.Many2one('hr.workwear.size', string='Размер')
    product_id = fields.Many2one('product.product', string='Номенклатура')
    quantity = fields.Float(string='Количество', default=1)
    wear_period_months = fields.Integer(string='Срок носки, мес.')
    expiry_date = fields.Date(compute='_compute_expiry_date', store=True, string='Истекает')
    is_returned = fields.Boolean(string='Возвращено')
    return_date = fields.Date(string='Дата возврата')
    condition = fields.Selection([
        ('good', 'Годна'),
        ('worn', 'Износ'),
        ('writeoff', 'Списание'),
    ], string='Состояние')

    @api.depends('issue_date', 'wear_period_months')
    def _compute_expiry_date(self):
        for line in self:
            if line.issue_date and line.wear_period_months:
                line.expiry_date = line.issue_date + relativedelta(months=line.wear_period_months)
            else:
                line.expiry_date = False

    @api.onchange('type_id')
    def _onchange_type_id(self):
        if not self.type_id:
            return
        self.product_id = self.type_id.product_id
        self.wear_period_months = self.type_id.wear_period_months
        employee = self.issue_id.employee_id
        size = False
        if employee:
            size = employee.workwear_size_ids.filtered(lambda s: s.type_id == self.type_id)[:1].size_id
        self.size_id = size.id if size else False
        if employee and not size:
            return {'warning': {
                'title': _('Размер не задан'),
                'message': _('У сотрудника %s не указан размер для "%s" - выдать можно, '
                             'размер впишите позже на карточке сотрудника.') % (
                                 employee.name, self.type_id.name),
            }}

    def write(self, vals):
        res = super().write(vals)
        if 'is_returned' in vals:
            self.issue_id._sync_return_state()
        return res
