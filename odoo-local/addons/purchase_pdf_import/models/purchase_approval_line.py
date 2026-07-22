from odoo import api, fields, models
from odoo.exceptions import UserError


class PurchaseApprovalLine(models.Model):
    _name = 'purchase.approval.line'
    _description = 'Строка согласования закупки'
    _order = 'sequence, id'

    purchase_order_id = fields.Many2one(
        'purchase.order', string='Закупка', required=True, ondelete='cascade')
    sequence = fields.Integer(string='Очерёдность', default=10)
    approver_id = fields.Many2one('res.users', string='Согласующий', required=True)
    state = fields.Selection([
        ('pending', 'Ожидает'),
        ('approved', 'Согласовал'),
        ('refused', 'Отклонил'),
    ], string='Статус', default='pending', required=True)
    decision_date = fields.Datetime(string='Дата решения')
    comment = fields.Char(string='Комментарий')

    _sql_constraints = [
        ('approver_order_uniq', 'unique(purchase_order_id, approver_id)',
         'Этот согласующий уже добавлен в список по данной закупке.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        # action_send_to_approval рассылает активность только один раз, в
        # момент отправки на согласование - согласующего, добавленного
        # ПОСЛЕ этого момента (см. purchase_order.py: главный закупщик
        # может доба­влять/убирать согласующих в любое время), никто не
        # уведомлял вообще, он узнавал о заявке только случайно, зайдя в
        # заказ вручную (обнаружено 2026-07-14 - Боровику не пришла
        # активность, хотя его добавили Владислав уже после отправки).
        for line in lines:
            if line.state == 'pending' and line.purchase_order_id.approval_state == 'to_approve':
                line.purchase_order_id._notify_approver(line)
        return lines

    def action_approve(self):
        for line in self:
            if line.approver_id != self.env.user:
                raise UserError('Согласовать может только назначенный согласующий.')
            if line.state != 'pending':
                raise UserError('Повторное голосование запрещено.')
            if line.purchase_order_id.approval_state != 'to_approve':
                raise UserError(
                    'Заказ ещё не отправлен на согласование - нажмите '
                    '"Отправить на согласование" на самом заказе, прежде '
                    'чем голосовать по строкам.')
            line.write({
                'state': 'approved',
                'decision_date': fields.Datetime.now(),
            })
            line.purchase_order_id.sudo().message_post(
                body=f'{self.env.user.name} согласовал(а) закупку.')
            # _close_approval_activities (см. _check_all_approved ниже)
            # закрывает активности только когда ВСЕ согласующие проголосовали
            # - если кто-то один одобрил рано, а другие ещё нет, его
            # собственная "Требуется согласование закупки" продолжала висеть
            # так, будто он ничего не сделал (обнаружено 2026-07-15:
            # Владислав одобрил P00053/P00070 ещё вчера, но активность
            # осталась, пока Гибиева не проголосовала). Закрываем именно
            # СВОЮ активность сразу, не дожидаясь остальных.
            line.purchase_order_id.sudo().activity_ids.filtered(
                lambda a: a.summary == 'Требуется согласование закупки'
                and a.user_id == line.approver_id
            ).unlink()
        self.mapped('purchase_order_id')._check_all_approved()

    def action_refuse(self):
        self.ensure_one()
        if self.approver_id != self.env.user:
            raise UserError('Отклонить может только назначенный согласующий.')
        if self.state != 'pending':
            raise UserError('Повторное голосование запрещено.')
        if self.purchase_order_id.approval_state != 'to_approve':
            raise UserError(
                'Заказ ещё не отправлен на согласование - нажмите '
                '"Отправить на согласование" на самом заказе, прежде чем '
                'голосовать по строкам.')
        if not self.comment:
            raise UserError('Укажите причину отклонения в комментарии.')
        self.write({
            'state': 'refused',
            'decision_date': fields.Datetime.now(),
        })
        self.purchase_order_id._apply_decline(
            f'{self.env.user.name} отклонил(а) закупку: {self.comment}')
