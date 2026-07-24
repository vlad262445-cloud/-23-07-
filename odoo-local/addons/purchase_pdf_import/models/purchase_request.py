from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PurchaseRequestDepartment(models.Model):
    _name = 'purchase.request.department'
    _description = 'Отдел/участок (для запросов КП)'

    name = fields.Char(required=True)


class PurchaseRequestLine(models.Model):
    _name = 'purchase.request.line'
    _description = 'Строка запроса КП'

    request_id = fields.Many2one('purchase.request', required=True, ondelete='cascade')
    name = fields.Char(string='Наименование', required=True)
    spec = fields.Char(string='Доп. характеристика (ГОСТ)')
    product_qty = fields.Float(string='Кол-во', default=1.0)
    uom_label = fields.Char(
        string='Ед. изм.',
        help='Свободный текст (шт, м, п.м., кг и т.д.) - заявитель сам пишет, '
             'в какой единице указано количество, без привязки к справочнику '
             'единиц измерения Odoo.')
    note = fields.Text(string='Примечание')
    product_code = fields.Char(string='Код товара/артикул')


class PurchaseRequest(models.Model):
    _name = 'purchase.request'
    _description = 'Запрос КП'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(string='Номер', default=lambda self: _('Новый'), copy=False, readonly=True)
    requested_by = fields.Many2one(
        'res.users', string='Заявитель', default=lambda self: self.env.user, tracking=True)
    department_id = fields.Many2one(
        'purchase.request.department', string='Отдел/участок',
        default=lambda self: self.env.user.purchase_department_id)
    desired_date = fields.Date(string='Желательная дата')
    cost_code = fields.Char(
        string='Код затрат',
        help='Необязательное поле. Если заявитель знает код статьи затрат '
             '(например, из бумажного бланка) - можно указать его здесь для '
             'справки закупщику. Если код неизвестен - оставьте пустым.')
    line_ids = fields.One2many('purchase.request.line', 'request_id', string='Позиции')
    items_preview = fields.Char(string='Товары (кратко)', compute='_compute_items_preview')
    state = fields.Selection([
        ('requested', 'Запрошено'),
        ('in_progress', 'В работе'),
        ('invoice_generated', 'Счёт сформирован'),
        ('to_approve', 'На согласовании'),
        ('approved', 'Согласовано'),
        ('partial_paid', 'Предоплата 50%'),
        ('invoice_paid', 'Счёт оплачен'),
        ('in_transit', 'В пути'),
        ('in_stock', 'На складе'),
        # Отдельный терминальный статус, вне трёх маршрутов оплаты ниже -
        # заявка сюда попадает только через "Объединить заявки" (см.
        # wizard/request_merge_wizard.py), не через обычный жизненный цикл.
        # Намеренно НЕ добавлен в state_full_prepay/_split_50_50/_post_payment -
        # это состояние-съезд с дороги, а не шаг статус-бара; вьюха вместо
        # статус-бара показывает для него отдельную плашку (web_ribbon).
        ('merged', 'Объединена'),
    ], string='Статус', default='requested', tracking=True, copy=False, required=True)
    purchase_order_id = fields.Many2one(
        'purchase.order', string='Заказ на закупку', readonly=True, copy=False)
    split_from_request_id = fields.Many2one(
        'purchase.request', string='Разделено из', readonly=True, copy=False,
        help='Заполняется автоматически, если эта заявка появилась в результате '
             'разделения другой заявки на несколько (см. "Разделить заявку").')
    root_request_id = fields.Many2one(
        'purchase.request', string='Корневая заявка', compute='_compute_root_request_id',
        store=True, help='Сама заявка, если она не была разделена ни из чего, иначе - '
                          'самая первая заявка в цепочке разделений.')
    related_request_count = fields.Integer(
        string='Связанные заявки', compute='_compute_related_request_count')
    merged_into_request_id = fields.Many2one(
        'purchase.request', string='Объединена в', readonly=True, copy=False,
        help='Заполняется автоматически, если позиции этой заявки были перенесены '
             'в другую заявку через "Объединить заявки" (например, мелкая позиция '
             'сама по себе не набирала минимальную сумму заказа поставщика).')
    merged_donor_ids = fields.One2many(
        'purchase.request', 'merged_into_request_id', string='Объединённые заявки',
        help='Заявки, чьи позиции были перенесены в эту через "Объединить заявки".')
    merged_donor_count = fields.Integer(
        string='Объединённые заявки', compute='_compute_merged_donor_count')
    is_unlocked = fields.Boolean(
        string='Разблокирована', default=False, copy=False,
        help='Заявка автоматически блокируется от правок, как только у неё '
             'появляется заказ (purchase_order_id) - чтобы список позиций не '
             'разошёлся молча с уже созданным заказом. Это временное '
             'исключение на одно сохранение: после следующей правки заявка '
             'снова блокируется сама (см. write()).')
    can_unlock = fields.Boolean(
        string='Может разблокировать', compute='_compute_can_unlock')

    @api.depends_context('uid')
    def _compute_can_unlock(self):
        user = self.env.user
        is_chief_buyer = user.has_group('purchase_pdf_import.group_chief_buyer')
        for request in self:
            request.can_unlock = is_chief_buyer or request.requested_by == user

    def action_unlock(self):
        self.ensure_one()
        self.write({'is_unlocked': True})

    # Поля, правка которых блокируется после появления purchase_order_id -
    # ключ намеренно УЗКИЙ (не "любая запись, пока есть заказ"), потому что
    # системные переходы статуса (_advance_request_state, приёмка на складе
    # и т.д.) тоже пишут в purchase.request уже ПОСЛЕ появления заказа, но
    # трогают только 'state' - широкая проверка сломала бы, например,
    # подтверждение приёмки кладовщиком (stock_picking.py, без sudo()).
    _LOCKED_FIELDS = {'requested_by', 'department_id', 'desired_date', 'cost_code', 'note', 'line_ids'}
    _HEADER_DIFF_FIELDS = [
        ('requested_by', 'Заявитель'),
        ('department_id', 'Отдел/участок'),
        ('desired_date', 'Желательная дата'),
        ('cost_code', 'Код затрат'),
        ('note', 'Комментарий'),
    ]
    _LINE_DIFF_FIELDS = [
        ('name', 'Наименование'),
        ('product_qty', 'Кол-во'),
        ('spec', 'Доп. характеристика'),
        ('uom_label', 'Ед. изм.'),
        ('note', 'Примечание'),
        ('product_code', 'Код товара/артикул'),
    ]

    def _snapshot_for_diff(self):
        self.ensure_one()
        return {
            'header': {fname: getattr(self, fname) for fname, _label in self._HEADER_DIFF_FIELDS},
            'lines': {
                line.id: {fname: getattr(line, fname) for fname, _label in self._LINE_DIFF_FIELDS}
                for line in self.line_ids
            },
        }

    def _log_diff(self, before):
        from markupsafe import Markup

        self.ensure_one()
        header_labels = dict(self._HEADER_DIFF_FIELDS)
        line_labels = dict(self._LINE_DIFF_FIELDS)
        parts = []

        for fname, before_val in before['header'].items():
            after_val = getattr(self, fname)
            if before_val != after_val:
                parts.append(Markup('%s: "%s" → "%s"') % (header_labels[fname], before_val or '', after_val or ''))

        after_lines = {
            line.id: {fname: getattr(line, fname) for fname, _label in self._LINE_DIFF_FIELDS}
            for line in self.line_ids
        }
        for line_id, after_vals in after_lines.items():
            if line_id not in before['lines']:
                parts.append(Markup('добавлена позиция "%s"') % after_vals['name'])
                continue
            before_vals = before['lines'][line_id]
            for fname, before_val in before_vals.items():
                after_val = after_vals[fname]
                if before_val != after_val:
                    label = before_vals.get('name') or after_vals.get('name') or ''
                    parts.append(Markup('%s - %s: "%s" → "%s"') % (
                        label, line_labels[fname], before_val or '', after_val or ''))
        for line_id, before_vals in before['lines'].items():
            if line_id not in after_lines:
                parts.append(Markup('удалена позиция "%s"') % before_vals['name'])

        if parts:
            self.message_post(body=Markup('Изменения после разблокировки:<br/>') + Markup('<br/>').join(parts))

    def write(self, vals):
        touched = self._LOCKED_FIELDS & set(vals)

        if vals.get('is_unlocked'):
            for request in self:
                if not request.can_unlock:
                    raise UserError(_(
                        'Разблокировать заявку может только заявитель или Главный закупщик.'))

        if touched:
            for request in self:
                if request.purchase_order_id and not request.is_unlocked:
                    raise UserError(_(
                        'Заявка уже оформлена в заказ и заблокирована от изменений - '
                        'нажмите "Разблокировать", если правка действительно нужна.'))

        snapshots = {}
        if touched:
            for request in self:
                if request.purchase_order_id:
                    snapshots[request.id] = request._snapshot_for_diff()

        res = super().write(vals)

        for request in self:
            before = snapshots.get(request.id)
            if before is not None:
                request._log_diff(before)

        # Разблокировка - разовая, на одно сохранение: как только правка
        # сохранена, заявка сама снова блокируется, а не остаётся открытой
        # навсегда после одной случайной разблокировки.
        if touched and 'is_unlocked' not in vals:
            for request in self:
                if request.purchase_order_id:
                    super(PurchaseRequest, request).write({'is_unlocked': False})

        return res

    @api.depends('merged_donor_ids')
    def _compute_merged_donor_count(self):
        for request in self:
            request.merged_donor_count = len(request.merged_donor_ids)

    def action_view_merged_donors(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Объединённые заявки'),
            'res_model': 'purchase.request',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.merged_donor_ids.ids)],
        }

    def action_view_merge_target(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.request',
            'res_id': self.merged_into_request_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    @api.depends('split_from_request_id')
    def _compute_root_request_id(self):
        for request in self:
            request.root_request_id = request.split_from_request_id or request

    @api.depends('root_request_id')
    def _compute_related_request_count(self):
        # Считаем через root_request_id, а не через прямых "детей" - разделить
        # можно и уже дочернюю заявку, тогда её собственные дети всё равно
        # должны попасть в одну "семью" с самой первой заявкой, а не
        # образовать отдельную ветку (см. action_split в мастере разделения).
        for request in self:
            if not request.id:
                request.related_request_count = 0
                continue
            request.related_request_count = self.search_count([
                ('root_request_id', '=', request.root_request_id.id),
                ('id', '!=', request.id),
            ])

    def action_view_related_requests(self):
        self.ensure_one()
        family = self.search([
            ('root_request_id', '=', self.root_request_id.id),
            ('id', '!=', self.id),
        ])
        return {
            'type': 'ir.actions.act_window',
            'name': _('Связанные заявки'),
            'res_model': 'purchase.request',
            'view_mode': 'list,form',
            'domain': [('id', 'in', family.ids)],
        }

    def action_open_split_wizard(self):
        self.ensure_one()
        if self.purchase_order_id:
            raise UserError(_(
                'Заявка уже оформлена в заказ - разделять её поздно. '
                'Разделить заявку можно только до нажатия "Оформить заказ".'))
        if self.state == 'merged':
            raise UserError(_(
                'Заявка объединена с %s и не содержит своих позиций - '
                'разделять нечего.') % self.merged_into_request_id.name)
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.request.split.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_request_id': self.id},
        }
    payment_type = fields.Selection(
        related='purchase_order_id.payment_type', string='Тип оплаты')
    order_approval_state = fields.Selection(
        related='purchase_order_id.approval_state', string='Статус согласования заказа')
    can_arrange_delivery = fields.Boolean(related='purchase_order_id.can_arrange_delivery')
    note = fields.Text(string='Комментарий')

    def action_arrange_delivery(self):
        self.ensure_one()
        self.purchase_order_id.action_arrange_delivery()

    # widget="statusbar" всегда показывает шаги в том порядке, в котором они
    # объявлены в Python (statusbar_visible только фильтрует, какие показать,
    # но не переставляет их) - поэтому для трёх разных маршрутов оплаты
    # нужны три отдельных поля с нужным порядком объявления, а не одно поле
    # с разными statusbar_visible.
    state_full_prepay = fields.Selection([
        ('requested', 'Запрошено'),
        ('in_progress', 'В работе'),
        ('invoice_generated', 'Счёт сформирован'),
        ('to_approve', 'На согласовании'),
        ('approved', 'Согласовано'),
        ('invoice_paid', 'Счёт оплачен'),
        ('in_transit', 'В пути'),
        ('in_stock', 'На складе'),
    ], string='Статус', compute='_compute_state_display')
    state_split_50_50 = fields.Selection([
        ('requested', 'Запрошено'),
        ('in_progress', 'В работе'),
        ('invoice_generated', 'Счёт сформирован'),
        ('to_approve', 'На согласовании'),
        ('approved', 'Согласовано'),
        ('partial_paid', 'Предоплата 50%'),
        ('in_transit', 'В пути'),
        ('in_stock', 'На складе'),
        ('invoice_paid', 'Счёт оплачен'),
    ], string='Статус', compute='_compute_state_display')
    state_post_payment = fields.Selection([
        ('requested', 'Запрошено'),
        ('in_progress', 'В работе'),
        ('invoice_generated', 'Счёт сформирован'),
        ('to_approve', 'На согласовании'),
        ('approved', 'Согласовано'),
        ('in_transit', 'В пути'),
        ('in_stock', 'На складе'),
        ('invoice_paid', 'Счёт оплачен'),
    ], string='Статус', compute='_compute_state_display')

    @api.depends('state')
    def _compute_state_display(self):
        # 'partial_paid' существует только в маршруте 50/50 - если текущий
        # статус недопустим для конкретного варианта поля, оставляем его
        # пустым (это поле всё равно скрыто, пока payment_type не совпадает).
        full_prepay_states = dict(self._fields['state_full_prepay'].selection)
        split_states = dict(self._fields['state_split_50_50'].selection)
        post_payment_states = dict(self._fields['state_post_payment'].selection)
        for request in self:
            request.state_full_prepay = request.state if request.state in full_prepay_states else False
            request.state_split_50_50 = request.state if request.state in split_states else False
            request.state_post_payment = request.state if request.state in post_payment_states else False

    @api.constrains('department_id')
    def _check_department_matches_user(self):
        # Подстраховка на случай прямого API-вызова в обход readonly во
        # вьюхе - обычный сотрудник со своим "домашним" отделом не может
        # сохранить заявку с другим отделом, даже если как-то обошёл форму.
        user = self.env.user
        if user.has_group('purchase.group_purchase_user') \
                or user.has_group('purchase.group_purchase_manager') \
                or user.has_group('purchase_pdf_import.group_chief_buyer') \
                or user.has_group('purchase_pdf_import.group_observer'):
            return
        if not user.purchase_department_id:
            return
        for request in self:
            if request.department_id and request.department_id != user.purchase_department_id:
                raise ValidationError(_(
                    'Вы можете подавать заявки только по своему отделу/участку (%s).'
                ) % user.purchase_department_id.name)

    @api.constrains('line_ids', 'state')
    def _check_has_lines(self):
        for request in self:
            # 'merged' - единственное состояние, где пустая заявка ожидаема
            # и правильна: "Объединить заявки" намеренно опустошает донора,
            # перенося все его позиции на заявку-цель (см. request_merge_wizard.py).
            if not request.line_ids and request.state != 'merged':
                raise ValidationError(_(
                    "Добавьте хотя бы одну позицию в заявку перед сохранением - "
                    "пустая заявка не имеет смысла."
                ))

    @api.depends('line_ids.name', 'line_ids.product_qty')
    def _compute_items_preview(self):
        for request in self:
            request.items_preview = ', '.join(
                f"{line.product_qty:g}× {line.name}" for line in request.line_ids if line.name
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals.get('name') == _('Новый'):
                vals['name'] = self.env['ir.sequence'].next_by_code('purchase.request') or _('Новый')
        return super().create(vals_list)

    def action_view_details(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.request',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_pdf_import_wizard(self):
        self.ensure_one()
        if self.state == 'merged':
            raise UserError(_(
                'Заявка объединена с %s и не содержит своих позиций - '
                'оформлять заказ по ней нельзя.') % self.merged_into_request_id.name)
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.pdf.import.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_request_id': self.id},
        }

    def action_view_order(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'res_id': self.purchase_order_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_payment_import_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.payment.import.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_request_id': self.id,
                'default_purchase_order_id': self.purchase_order_id.id,
            },
        }
