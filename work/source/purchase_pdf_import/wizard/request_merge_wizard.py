from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PurchaseRequestMergeWizard(models.TransientModel):
    _name = 'purchase.request.merge.wizard'
    _description = 'Объединение заявок на закупку'

    request_ids = fields.Many2many(
        'purchase.request', string='Выбранные заявки', readonly=True)
    target_request_id = fields.Many2one(
        'purchase.request', string='Объединить в', required=True,
        domain="[('id', 'in', request_ids)]")
    reason = fields.Text(
        string='Причина объединения', required=True,
        help='Попадёт в чат каждой заявки-донора и заявки-цели - тот, кто '
             'подавал донора, должен понимать, почему его позиции забрали '
             'именно в эту заявку, а не просто увидеть факт переноса.')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_ids = self.env.context.get('active_ids') or []
        if 'request_ids' in fields_list:
            res['request_ids'] = [(6, 0, active_ids)]
        return res

    @staticmethod
    def _format_items(lines):
        return ', '.join(f'{line.product_qty:g}× {line.name}' for line in lines) or _('(нет позиций)')

    def action_merge(self):
        from markupsafe import Markup

        self.ensure_one()
        requests = self.request_ids
        if len(requests) < 2:
            raise UserError(_('Нужно выбрать хотя бы две заявки для объединения.'))
        target = self.target_request_id
        if target not in requests:
            raise UserError(_('Заявка-цель должна быть среди выбранных заявок.'))
        donors = requests - target
        if not donors:
            raise UserError(_('Нечего объединять - в выборке только заявка-цель.'))
        if target.purchase_order_id:
            raise UserError(_(
                'Заявка-цель уже оформлена в заказ - объединять в неё больше нельзя.'))
        if target.state == 'merged':
            raise UserError(_(
                'Заявка-цель сама уже объединена в другую заявку - выберите другую цель.'))
        for donor in donors:
            if donor.purchase_order_id:
                raise UserError(_(
                    'Заявка %s уже оформлена в заказ - объединять её нельзя.') % donor.name)
            if donor.state == 'merged':
                raise UserError(_(
                    'Заявка %s уже объединена ранее - выберите другую.') % donor.name)

        # Позиции каждого донора нужно посчитать ДО переноса строк на цель -
        # после переноса donor.line_ids уже пуст. Без этого в чате был бы
        # виден только номер заявки-донора, а не то, что конкретно из неё
        # уехало - человеку, подавшему донора, этого недостаточно, чтобы
        # понять, куда делась именно его линейка/деталь.
        donor_items = {donor.id: self._format_items(donor.line_ids) for donor in donors}
        donor_lines = donors.mapped('line_ids')
        for donor in donors:
            # Сначала помечаем донора объединённым и только потом переносим
            # его строки на цель. Перенос строк ниже реализован как запись
            # (4, id) НА ЦЕЛИ - она переподвешивает line.request_id и заново
            # проверяет _check_has_lines у донора при следующем flush; если
            # donor.state ещё не 'merged' в этот момент, констрейнт упадёт
            # на внезапно опустевшей заявке. Порядок здесь принципиален.
            donor.write({
                'state': 'merged',
                'merged_into_request_id': target.id,
            })
        target.write({'line_ids': [(4, line.id) for line in donor_lines]})

        for donor in donors:
            donor.message_post(body=Markup(
                'Заявка объединена с %s.<br/>Перенесены позиции: %s.<br/>Причина: %s'
            ) % (target.name, donor_items[donor.id], self.reason))

        target_body = Markup('В эту заявку объединены:<br/>') + Markup('<br/>').join(
            Markup('из %s: %s') % (donor.name, donor_items[donor.id]) for donor in donors
        ) + Markup('<br/>Причина: %s') % self.reason
        target.message_post(body=target_body)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.request',
            'res_id': target.id,
            'view_mode': 'form',
            'target': 'current',
        }
