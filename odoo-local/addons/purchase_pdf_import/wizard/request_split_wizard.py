from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PurchaseRequestSplitWizard(models.TransientModel):
    _name = 'purchase.request.split.wizard'
    _description = 'Разделение заявки на закупку'

    request_id = fields.Many2one('purchase.request', required=True, readonly=True)
    line_ids = fields.One2many(
        'purchase.request.split.wizard.line', 'wizard_id', string='Позиции')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        request_id = self.env.context.get('default_request_id')
        if request_id and 'line_ids' in fields_list:
            request = self.env['purchase.request'].browse(request_id)
            res['line_ids'] = [(0, 0, {
                'request_line_id': line.id,
                'name': line.name,
                'product_qty': line.product_qty,
                'selected': True,
            }) for line in request.line_ids]
        return res

    def _next_child_name(self, root):
        # Считаем суффикс по всей "семье" (родитель + все уже отделённые от
        # него заявки), а не просто по прямым детям root - иначе разделение
        # уже дочерней заявки могло бы случайно повторить уже занятый номер.
        family = self.env['purchase.request'].search([('root_request_id', '=', root.id)])
        # Родитель сам по себе не несёт суффикса в имени - считаем его "-1"
        # только для целей нумерации, чтобы самый первый реальный ребёнок
        # получил "-2", а не "-1" (что выглядело бы как будто пропущен один).
        existing_suffixes = [1]
        for candidate in family:
            _prefix, _sep, suffix = candidate.name.rpartition('-')
            if suffix.isdigit():
                existing_suffixes.append(int(suffix))
        return '%s-%d' % (root.name, max(existing_suffixes) + 1)

    def action_split(self):
        from markupsafe import Markup

        self.ensure_one()
        request = self.request_id
        if request.purchase_order_id:
            raise UserError(_(
                'Заявка уже оформлена в заказ - разделять её поздно. '
                'Разделить заявку можно только до нажатия "Оформить заказ".'))
        selected = self.line_ids.filtered('selected')
        remaining = self.line_ids - selected
        if not selected:
            raise UserError(_('Отметьте хотя бы одну позицию для переноса в новую заявку.'))
        if not remaining:
            raise UserError(_(
                'Нельзя перенести все позиции - в исходной заявке должна остаться '
                'хотя бы одна. Если нужно перенести всё, разделение не требуется.'))

        root = request.root_request_id
        child_name = self._next_child_name(root)
        child = self.env['purchase.request'].create({
            'name': child_name,
            'requested_by': request.requested_by.id,
            'department_id': request.department_id.id,
            'desired_date': request.desired_date,
            'cost_code': request.cost_code,
            'split_from_request_id': root.id,
            'line_ids': [(4, line.request_line_id.id) for line in selected],
        })
        request.message_post(body=_(
            'Заявка разделена - часть позиций перенесена в %s.') % child.name)
        # Обычное сообщение легко потерять в общей ленте чата - а без него
        # непонятно, почему в новой заявке вдруг только часть позиций и
        # почему у неё такой странный номер (см. _next_child_name), поэтому
        # для дочерней заявки сообщение должно бросаться в глаза.
        child.message_post(body=Markup(
            '<p style="color:#a94442;font-weight:bold;">'
            '!!! ВНИМАНИЕ: ЗАЯВКА СОЗДАНА РАЗДЕЛЕНИЕМ ИЗ %s !!!</p>'
        ) % request.name)
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.request',
            'res_id': request.id,
            'view_mode': 'form',
            'target': 'current',
        }


class PurchaseRequestSplitWizardLine(models.TransientModel):
    _name = 'purchase.request.split.wizard.line'
    _description = 'Позиция для разделения заявки'

    wizard_id = fields.Many2one(
        'purchase.request.split.wizard', required=True, ondelete='cascade')
    request_line_id = fields.Many2one('purchase.request.line', required=True, readonly=True)
    name = fields.Char(string='Наименование', readonly=True)
    product_qty = fields.Float(string='Кол-во', readonly=True)
    selected = fields.Boolean(string='Перенести в новую заявку', default=True)
