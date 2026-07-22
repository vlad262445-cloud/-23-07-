from odoo import fields, models


class PurchaseUpddLine(models.Model):
    _name = 'purchase.updd.line'
    _description = 'УПД по закупке'
    _order = 'id desc'

    purchase_order_id = fields.Many2one(
        'purchase.order', string='Закупка', ondelete='cascade')
    picking_id = fields.Many2one(
        'stock.picking', string='Приёмка (без заказа)', ondelete='cascade',
        help='Заполняется вместо "Закупки", когда УПД подтверждён без привязки '
             'к заказу - товар пришёл в обход обычного процесса, и мастер '
             'сам создал и провёл приёмку напрямую по составу УПД.')
    updd_date = fields.Char(string='Дата УПД (по документу)')
    updd_number = fields.Char(string='Номер УПД')
    amount = fields.Float(string='Сумма')
    seller_name = fields.Char(string='Продавец (по документу)')
    seller_inn = fields.Char(string='ИНН продавца (по документу)')
    partner_matched = fields.Boolean(string='Сверено с контрагентом')
    attachment_id = fields.Many2one('ir.attachment', string='УПД')

    # ИИ иногда неверно распознаёт номер/дату/продавца/ИНН/сумму по фото
    # документа, а исправить это раньше было негде - см. action_open_correction_form.
    # У этой модели нет своего mail.thread, поэтому лог правок пишем на
    # родителя (заказ или приёмку без заказа), тем же паттерном, что уже
    # используется в purchase.request._snapshot_for_diff/_log_diff.
    _DIFF_FIELDS = [
        ('updd_number', 'Номер УПД'),
        ('updd_date', 'Дата УПД'),
        ('amount', 'Сумма'),
        ('seller_name', 'Продавец'),
        ('seller_inn', 'ИНН продавца'),
        ('partner_matched', 'Сверено с контрагентом'),
    ]

    def _diff_display(self, fname, value):
        if fname == 'partner_matched':
            return 'да' if value else 'нет'
        return value or ''

    def _snapshot_for_diff(self):
        self.ensure_one()
        return {fname: getattr(self, fname) for fname, _label in self._DIFF_FIELDS}

    def _log_diff(self, before):
        from markupsafe import Markup

        self.ensure_one()
        labels = dict(self._DIFF_FIELDS)
        parts = []
        for fname, before_val in before.items():
            after_val = getattr(self, fname)
            if before_val != after_val:
                parts.append(Markup('%s: "%s" → "%s"') % (
                    labels[fname], self._diff_display(fname, before_val), self._diff_display(fname, after_val)))
        if not parts:
            return
        parent = self.purchase_order_id or self.picking_id
        if not parent:
            return
        # sudo() - Кладовщик не имеет прямого права записи в заказ/приёмку
        # (только чтение), но кнопка-карандаш уже ограничена его ролью, так
        # что сама запись безопасна - та же логика, что и в action_skip_updd.
        parent.sudo().message_post(body=Markup('Исправлены данные УПД:<br/>') + Markup('<br/>').join(parts))

    def write(self, vals):
        touched = {fname for fname, _label in self._DIFF_FIELDS} & set(vals)
        snapshots = {line.id: line._snapshot_for_diff() for line in self} if touched else {}
        res = super().write(vals)
        for line in self:
            before = snapshots.get(line.id)
            if before is not None:
                line._log_diff(before)
        return res

    def action_open_correction_form(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.updd.line',
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': self.env.ref('purchase_pdf_import.view_purchase_updd_line_form').id,
            'target': 'new',
        }
