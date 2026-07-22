from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    cost_analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Статья затрат', tracking=True,
        domain=lambda self: ['!', ('plan_id', 'child_of', self.env.ref(
            'purchase_pdf_import.analytic_plan_category').id)],
        help='Обязательна для подтверждения закупки. Направление/тип продукции '
             '(001-008) сюда не входит - для него отдельное поле "Категория".')
    cost_category_id = fields.Many2one(
        'account.analytic.account', string='Категория', tracking=True,
        domain=lambda self: [('plan_id', '=', self.env.ref(
            'purchase_pdf_import.analytic_plan_category').id)],
        help='Направление/тип продукции (Самозарядные, Винтовки, Тюнинг и т.д.) - '
             'отдельная ось от "Статья затрат", обязательна для подтверждения закупки.')
    payment_type = fields.Selection([
        ('full_prepay', 'Полная предоплата'),
        ('split_50_50', '50% предоплата + 50% после получения'),
        ('post_payment', 'Оплата после получения'),
    ], string='Тип оплаты', tracking=True,
        help='Определяет, в каком порядке идут этапы оплаты и получения '
             'в статусе заявки. Указывается при оформлении заказа из PDF.')
    request_ids = fields.One2many(
        'purchase.request', 'purchase_order_id', string='Заявки КП')
    approval_line_ids = fields.One2many(
        'purchase.approval.line', 'purchase_order_id', string='Согласующие')
    payment_line_ids = fields.One2many(
        'purchase.payment.line', 'purchase_order_id', string='Платежи')
    approval_state = fields.Selection([
        ('none', 'Без согласования'),
        ('to_approve', 'На согласовании'),
        ('approved', 'Согласована'),
        ('declined', 'Отклонена'),
    ], string='Статус согласования', default='none', tracking=True, copy=False)
    is_current_user_approver = fields.Boolean(
        string='Я согласующий', compute='_compute_is_current_user_approver')
    decline_reason = fields.Text(string='Причина отклонения', tracking=True)
    document_status = fields.Selection([
        ('done', 'Документы в порядке'),
        ('blocked', 'Не хватает документа'),
    ], string='Документы', compute='_compute_document_status', store=True)
    document_status_note = fields.Char(compute='_compute_document_status', store=True)
    pending_action_note = fields.Char(
        string='Что сейчас требуется', compute='_compute_pending_action_note')

    @api.depends(
        'state', 'approval_state', 'approval_line_ids.state', 'approval_line_ids.approver_id',
        'decline_reason', 'cost_analytic_account_id', 'cost_category_id', 'payment_type',
        'can_arrange_delivery', 'updd_relevant', 'updd_line_ids', 'updd_skipped',
        'document_status', 'document_status_note')
    def _compute_pending_action_note(self):
        # Владислав (Главный закупщик) смотрел стандартный список "Заказы на
        # закупку" в самом Odoo (не наш "Реестр закупок") - там виден только
        # родной статус Odoo ("Запрос на коммерческое предложение" и т.п.),
        # который вообще не отражает наш процесс согласования/оплаты/УПД, и
        # было непонятно, требуется ли от него какое-то действие. Это поле -
        # одна короткая строка "от кого и что нужно" поверх уже существующей
        # логики (can_arrange_delivery/updd_relevant/document_status), без
        # дублирования самих правил.
        for order in self:
            if not isinstance(order.id, int):
                order.pending_action_note = False
                continue
            if order.state == 'cancel':
                order.pending_action_note = _('Заказ отменён.')
                continue
            if order.approval_state == 'declined':
                order.pending_action_note = (
                    _('Закупка отклонена: %s') % order.decline_reason
                    if order.decline_reason else _('Закупка отклонена - требуется решение закупщика.'))
                continue
            if order.approval_state == 'none':
                missing = []
                if not order.cost_analytic_account_id:
                    missing.append(_('статью затрат'))
                if not order.cost_category_id:
                    missing.append(_('категорию'))
                if not order.payment_type:
                    missing.append(_('тип оплаты'))
                if missing:
                    order.pending_action_note = _('Закупщику: указать %s.') % ', '.join(missing)
                else:
                    order.pending_action_note = _('Закупщику: отправить на согласование.')
                continue
            if order.approval_state == 'to_approve':
                pending = order.approval_line_ids.filtered(lambda l: l.state == 'pending').approver_id
                order.pending_action_note = _('Ожидает согласования: %s.') % (
                    ', '.join(pending.mapped('name')) if pending else '-')
                continue
            # approval_state == 'approved' дальше - следующий шаг зависит от
            # того, что уже реализовано в самом заказе.
            if order.can_arrange_delivery:
                order.pending_action_note = _('Закупщику: оформить доставку.')
            elif order.updd_relevant and not order.updd_line_ids and not order.updd_skipped:
                order.pending_action_note = _('Кладовщику: загрузить УПД (или отметить как пропущенный).')
            elif order.document_status == 'blocked':
                order.pending_action_note = order.document_status_note
            else:
                order.pending_action_note = False
    request_state = fields.Selection(
        selection=lambda self: self.env['purchase.request']._fields['state'].selection,
        string='Статус заявки', compute='_compute_request_state', store=True)

    @api.depends('request_ids.state')
    def _compute_request_state(self):
        for order in self:
            order.request_state = order.request_ids[:1].state if order.request_ids else False

    # Приоритет заказа - живёт на ЗАКАЗЕ, а не на заявке (изначально был на
    # заявке, перенесено 2026-07-17): это сигнал ВСЕМ сотрудникам, кто
    # соприкасается с этим заказом (закупщику, бухгалтеру, кладовщику), что
    # его нужно обработать в первую очередь - не только про оплату, хотя имя
    # поля (payment_priority) осталось от первой версии, где это была
    # подсказка только для бухгалтера. Само техническое имя поля не менял
    # (это уже структура БД, а не просто подпись) - только то, что видит
    # человек: подпись и текст ошибки.
    # Три уровня, не пять - шкала на 5 шагов на практике не используется
    # осмысленно, а числа без подписи неоднозначны ("1 - это самое срочное
    # или наименее срочное?"). Отдельное имя поля (не "priority") специально,
    # чтобы не конфликтовать с уже существующим родным priority у Odoo
    # (purchase.order.priority, 2 уровня Обычная/Срочно, 1 звезда) - тот
    # скрыт из формы заказа (см. views/purchase_order_priority_views.xml),
    # чтобы не путать два разных индикатора на одном экране.
    payment_priority = fields.Selection([
        ('0', 'Обычная'),
        ('1', 'Срочно'),
        ('2', 'Критично'),
    ], string='Приоритет заказа', default='0', tracking=True,
        help='Сигнал всем, кто работает с этим заказом (закупщик, бухгалтер, '
             'кладовщик), что его нужно обработать в первую очередь - не '
             'только про оплату.')

    @api.constrains('payment_priority')
    def _check_payment_priority_setter(self):
        # groups= во вьюхе - только косметика, реальная граница здесь (тот же
        # приём, что и у _check_department_matches_user в purchase_request.py).
        user = self.env.user
        if user.has_group('purchase.group_purchase_user') \
                or user.has_group('purchase_pdf_import.group_chief_buyer'):
            return
        for order in self:
            if order.payment_priority != '0':
                raise ValidationError(_(
                    'Приоритет заказа может менять только закупщик или Главный закупщик.'))

    # Три варианта с порядком шагов под конкретный тип оплаты - см. пояснение
    # в purchase.request про то, почему statusbar_visible не годится сама по
    # себе для смены порядка.
    request_state_full_prepay = fields.Selection([
        ('requested', 'Запрошено'),
        ('in_progress', 'В работе'),
        ('invoice_generated', 'Счёт сформирован'),
        ('to_approve', 'На согласовании'),
        ('approved', 'Согласовано'),
        ('invoice_paid', 'Счёт оплачен'),
        ('in_transit', 'В пути'),
        ('in_stock', 'На складе'),
    ], string='Статус заявки', compute='_compute_request_state_display')
    request_state_split_50_50 = fields.Selection([
        ('requested', 'Запрошено'),
        ('in_progress', 'В работе'),
        ('invoice_generated', 'Счёт сформирован'),
        ('to_approve', 'На согласовании'),
        ('approved', 'Согласовано'),
        ('partial_paid', 'Предоплата 50%'),
        ('in_transit', 'В пути'),
        ('in_stock', 'На складе'),
        ('invoice_paid', 'Счёт оплачен'),
    ], string='Статус заявки', compute='_compute_request_state_display')
    request_state_post_payment = fields.Selection([
        ('requested', 'Запрошено'),
        ('in_progress', 'В работе'),
        ('invoice_generated', 'Счёт сформирован'),
        ('to_approve', 'На согласовании'),
        ('approved', 'Согласовано'),
        ('in_transit', 'В пути'),
        ('in_stock', 'На складе'),
        ('invoice_paid', 'Счёт оплачен'),
    ], string='Статус заявки', compute='_compute_request_state_display')

    @api.depends('request_state')
    def _compute_request_state_display(self):
        full_prepay_states = dict(self._fields['request_state_full_prepay'].selection)
        split_states = dict(self._fields['request_state_split_50_50'].selection)
        post_payment_states = dict(self._fields['request_state_post_payment'].selection)
        for order in self:
            order.request_state_full_prepay = (
                order.request_state if order.request_state in full_prepay_states else False)
            order.request_state_split_50_50 = (
                order.request_state if order.request_state in split_states else False)
            order.request_state_post_payment = (
                order.request_state if order.request_state in post_payment_states else False)
    payment_skipped = fields.Boolean(
        string='Платёжка не прикреплена (пропущено)', copy=False, tracking=True)
    amount_paid = fields.Monetary(
        string='Оплачено', compute='_compute_amount_paid', currency_field='currency_id')
    updd_line_ids = fields.One2many(
        'purchase.updd.line', 'purchase_order_id', string='УПД')
    updd_skipped = fields.Boolean(
        string='УПД не прикреплён (пропущено)', copy=False, tracking=True)
    updd_relevant = fields.Boolean(compute='_compute_updd_relevant')

    @api.depends('request_state', 'payment_type')
    def _compute_updd_relevant(self):
        # "Загрузить УПД"/"Пропустить УПД" раньше были видны только при
        # request_state == 'in_stock' в точности - если оплата (см.
        # _payment_target_state) продвигала статус дальше, к "Счёт
        # оплачен", кнопки пропадали навсегда, даже если УПД так и не был
        # прикреплён (тот же класс бага, что и в _compute_document_status,
        # обнаружено 2026-07-14 на P00003). "Дошли до in_stock или дальше",
        # а не "именно сейчас в in_stock".
        for order in self:
            order.updd_relevant = order._request_state_reached('in_stock')

    can_arrange_delivery = fields.Boolean(compute='_compute_can_arrange_delivery')

    @api.depends('state', 'payment_type', 'request_state', 'approval_state')
    def _compute_can_arrange_delivery(self):
        # "В пути" раньше нигде не выставлялся - до сих пор заявка просто
        # молча перепрыгивала этот шаг сразу к "На складе" при закрытии
        # приёмки. Кнопка нужна, чтобы закупщик мог явно отметить момент,
        # когда доставка реально согласована с поставщиком/транспортом.
        #
        # request_state продвигается до 'approved' сразу после одобрения
        # (см. _sync_request_state), а дальше - до оплаты/предоплаты -
        # ждёт соответствующего платежа. Для маршрутов с оплатой до
        # отгрузки нельзя судить по одному только request_state, иначе
        # кнопка появится раньше реальной оплаты. Поэтому явно проверяем
        # approval_state и требуем конкретный предыдущий шаг для маршрута.
        for order in self:
            if order.state not in ('purchase', 'done') or order.approval_state != 'approved':
                order.can_arrange_delivery = False
                continue
            required_predecessor = {
                'full_prepay': 'invoice_paid',
                'split_50_50': 'partial_paid',
            }.get(order.payment_type)
            if required_predecessor:
                order.can_arrange_delivery = order.request_state == required_predecessor
            else:
                order.can_arrange_delivery = order.request_state == 'approved'

    def action_arrange_delivery(self):
        for order in self:
            order._advance_request_state(order.request_ids, 'in_transit')
            order.message_post(body=_('Доставка оформлена - товар в пути на склад.'))
            # Заявитель не имеет доступа к заказу - видит только свою заявку,
            # поэтому сообщение нужно продублировать и туда (Мицуков сообщил,
            # что видел "оплачено", но не видел вообще ничего про доставку).
            for request in order.request_ids:
                request.message_post(body=_('Доставка оформлена - товар в пути на склад.'))

    def action_open_quick_view(self):
        # Открывает урезанную read-only форму в модальном окне (target='new')
        # поверх реестра закупок - пользователи просили "развернуть и
        # посмотреть кратко", не уходя со страницы списка (не терять
        # фильтры/скролл переходом на полную форму и обратно).
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': self.env.ref('purchase_pdf_import.view_purchase_order_quick_view_form').id,
            'target': 'new',
        }

    def action_open_updd_import_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.updd.import.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_purchase_order_id': self.id},
        }

    def action_skip_updd(self):
        # sudo() - кладовщик не имеет прямого права записи в заказ (только
        # чтение), но кнопка уже ограничена его ролью, так что сама запись
        # безопасна.
        for order in self:
            order.sudo().updd_skipped = True
            order.sudo().message_post(body=_(
                'Загрузка УПД пропущена вручную - документ подтверждается вне ИИ-импорта.'))

    @api.depends('payment_line_ids.amount')
    def _compute_amount_paid(self):
        for order in self:
            order.amount_paid = sum(order.payment_line_ids.mapped('amount'))

    def _payment_target_state(self):
        """Куда должен продвинуться статус заявки после нового платежа,
        с учётом выбранного типа оплаты и того, сколько уже оплачено."""
        self.ensure_one()
        target = self._payment_target_state_raw()
        # "Оплата после получения" и финальный платёж по "50/50" по смыслу
        # маршрута должны идти ПОСЛЕ приёмки ("На складе") - но
        # _advance_request_state ставит статус напрямую, а не шаг за шагом,
        # поэтому платёж, загруженный раньше приёмки, тихо перепрыгивал
        # через "В пути"/"На складе" (обнаружено 2026-07-14 на P00003,
        # платёжка была загружена, а доставку никто не оформлял). Придержим
        # продвижение до "Счёт оплачен" здесь - как только приёмка всё же
        # будет подтверждена, _sync_request_state_from_picking сам продвинет
        # уже готовый платёж дальше. Полная предоплата не ограничиваем -
        # там оплата по смыслу маршрута идёт ДО доставки.
        if target == 'invoice_paid' and self.payment_type != 'full_prepay' \
                and not self._request_state_reached('in_stock'):
            return False
        return target

    def _payment_target_state_raw(self):
        self.ensure_one()
        if not self.amount_total:
            return 'invoice_paid'
        ratio = self.amount_paid / self.amount_total
        if self.payment_type == 'split_50_50':
            if ratio >= 0.95:
                return 'invoice_paid'
            if ratio >= 0.4:
                return 'partial_paid'
            return False
        if ratio >= 0.95:
            return 'invoice_paid'
        return False
    requester_id = fields.Many2one(
        'res.users', string='Заказчик', compute='_compute_requester_id', store=True)

    @api.depends('request_ids.requested_by')
    def _compute_requester_id(self):
        for order in self:
            order.requester_id = order.request_ids[:1].requested_by

    @api.depends('approval_line_ids.approver_id', 'approval_line_ids.state')
    def _compute_is_current_user_approver(self):
        for order in self:
            order.is_current_user_approver = bool(
                order.approval_line_ids.filtered(
                    lambda l: l.approver_id == self.env.user and l.state == 'pending'))

    def _request_state_reached(self, milestone):
        """request_state == milestone once caught the "мы уже прошли этот
        шаг" случаи только для маршрутов, где milestone - последний шаг
        (full_prepay: in_stock последний). Для split_50_50/post_payment
        in_stock идёт ДО invoice_paid, поэтому точное сравнение "==" тихо
        переставало срабатывать сразу после оплаты - там дальше некому
        было напомнить про непрокреплённый УПД (обнаружено 2026-07-14 на
        P00003, post_payment). Сравниваем позицию в последовательности
        конкретно ЭТОГО маршрута, как уже делает _advance_request_state."""
        self.ensure_one()
        if not self.request_state:
            return False
        field_name = {
            'split_50_50': 'state_split_50_50',
            'post_payment': 'state_post_payment',
        }.get(self.payment_type, 'state_full_prepay')
        order_sequence = [key for key, _label in self.env['purchase.request']._fields[field_name].selection]
        if self.request_state not in order_sequence:
            return False
        return order_sequence.index(self.request_state) >= order_sequence.index(milestone)

    def _has_recognized_invoice(self):
        self.ensure_one()
        if self.request_ids:
            return True
        return bool(self.env['ir.attachment'].search_count([
            ('res_model', '=', 'purchase.order'),
            ('res_id', '=', self.id),
            ('mimetype', '=', 'application/pdf'),
        ]))

    @api.depends(
        'request_ids', 'payment_line_ids.partner_matched', 'payment_skipped', 'state',
        'updd_line_ids', 'updd_skipped', 'request_state', 'payment_type')
    def _compute_document_status(self):
        for order in self:
            if not isinstance(order.id, int):
                order.document_status = 'done'
                order.document_status_note = False
                continue
            missing = []
            if not order._has_recognized_invoice():
                missing.append(_('счёт не был распознан через ИИ-импорт (позиции внесены вручную)'))
            if order.payment_line_ids.filtered(lambda l: not l.partner_matched):
                missing.append(_('есть платёж(и), где ИНН получателя не сверен с поставщиком'))
            if order.state in ('purchase', 'done') and not order.payment_line_ids:
                if order.payment_skipped:
                    missing.append(_(
                        'платёжка отмечена как пропущенная - прикрепите её, когда будет доступна'))
                else:
                    missing.append(_('платёжка не прикреплена и не отмечена как пропущенная'))
            if order._request_state_reached('in_stock') and not order.updd_line_ids:
                if order.updd_skipped:
                    missing.append(_(
                        'УПД отмечен как пропущенный - прикрепите его, когда будет доступен'))
                else:
                    missing.append(_('УПД не прикреплён и не отмечен как пропущенный'))
            if missing:
                order.document_status = 'blocked'
                order.document_status_note = _('Не хватает документов: %s.') % '; '.join(missing)
            else:
                order.document_status = 'done'
                order.document_status_note = False

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        for order in orders:
            order._add_chief_buyer_approval_lines()
        return orders

    def _add_chief_buyer_approval_lines(self):
        """Согласующими автоматически становятся все, у кого роль "Главный
        закупщик" - раньше согласующим ставился сам заявитель, но по факту
        в компании согласование делает отдельный человек, а не тот, кто
        подал заявку."""
        chief_buyers = self.env.ref('purchase_pdf_import.group_chief_buyer').users
        for order in self:
            existing = order.approval_line_ids.approver_id
            to_add = chief_buyers - existing
            for user in to_add:
                # sudo() - это автоматическое системное действие при
                # создании ЛЮБОГО заказа, а не что-то, что должно зависеть
                # от прав создавшего заказ пользователя на approval.line
                # (Главный закупщик сам создаёт заказ через "Оформить
                # заказ", но create() на purchase.approval.line ему не
                # выдан отдельно - и не должен быть, это не его действие).
                self.env['purchase.approval.line'].sudo().create({
                    'purchase_order_id': order.id,
                    'approver_id': user.id,
                })

    def button_confirm(self):
        for order in self:
            if not order.cost_analytic_account_id:
                raise UserError(_(
                    'Статья затрат не заполнена. Укажите статью затрат '
                    'перед подтверждением закупки.'))
            if not order.cost_category_id:
                raise UserError(_(
                    'Категория не заполнена. Укажите категорию '
                    'перед подтверждением закупки.'))
            if order.approval_line_ids and order.approval_state != 'approved':
                raise UserError(_(
                    'Сначала согласуйте закупку (см. вкладку «Согласование») '
                    '- подтверждение станет доступно автоматически после '
                    'одобрения всеми согласующими.'))
        return super().button_confirm()

    def action_send_to_approval(self):
        for order in self:
            if not order.cost_analytic_account_id:
                raise UserError(_(
                    'Статья затрат не заполнена. Укажите статью затрат '
                    'перед отправкой закупки на согласование.'))
            if not order.cost_category_id:
                raise UserError(_(
                    'Категория не заполнена. Укажите категорию '
                    'перед отправкой закупки на согласование.'))
            if not order.payment_type:
                raise UserError(_(
                    'Не указан тип оплаты. Укажите тип оплаты (полная '
                    'предоплата / 50 на 50 / после получения) перед '
                    'отправкой закупки на согласование.'))
            if not order.approval_line_ids:
                raise UserError(_(
                    'Добавьте хотя бы одного согласующего перед отправкой на согласование.'))
            order.approval_line_ids.filtered(lambda l: l.state != 'pending').write({
                'state': 'pending', 'decision_date': False, 'comment': False,
            })
            order.approval_state = 'to_approve'
            order._sync_request_state()
            for line in order.approval_line_ids:
                order._notify_approver(line)
            order.message_post(body=_('Закупка отправлена на согласование.'))

    def _notify_approver(self, line):
        self.ensure_one()
        self.activity_schedule(
            'mail.mail_activity_data_todo',
            summary=_('Требуется согласование закупки'),
            note=_('Закупка %s ожидает вашего решения.') % self.name,
            user_id=line.approver_id.id,
        )

    def _check_all_approved(self):
        for order in self:
            if order.approval_state != 'to_approve':
                continue
            if order.approval_line_ids and all(
                    line.state == 'approved' for line in order.approval_line_ids):
                order = order.sudo()
                order.approval_state = 'approved'
                order._close_approval_activities()
                if order.state not in ('purchase', 'done'):
                    order.button_confirm()
                order._sync_request_state()
                order.message_post(body=_('Закупка согласована всеми участниками.'))
                order._notify_accountants()

    def _notify_accountants(self):
        """Без этого бухгалтер узнаёт, что пора платить, только если сам
        зайдёт в реестр закупок и отфильтрует "Не хватает документа" -
        Лариса Романова (Главный бухгалтер) сообщила, что не получает
        никакого уведомления о согласованных закупках."""
        self.ensure_one()
        accountants = self.env.ref('purchase_pdf_import.group_accountant').users
        for user in accountants:
            self.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_('Требуется прикрепить платёжку'),
                note=_('Закупка %s согласована - нужно оплатить и прикрепить платёжку.') % self.name,
                user_id=user.id,
            )

    def _close_payment_activities(self):
        for order in self:
            order.activity_ids.filtered(
                lambda a: a.summary == _('Требуется прикрепить платёжку')).unlink()

    def _apply_decline(self, reason):
        for order in self:
            order = order.sudo()
            order.approval_state = 'declined'
            order.decline_reason = reason
            order._close_approval_activities()
            order._sync_request_state()
            order.message_post(body=reason)

    def _close_approval_activities(self):
        for order in self:
            order.activity_ids.filtered(
                lambda a: a.summary == _('Требуется согласование закупки')).unlink()

    def _advance_request_state(self, requests, target_state):
        """Продвигает статус заявки вперёд, но никогда не откатывает назад -
        нельзя допускать отката, если заявка уже прошла дальше (например,
        оплачена) к моменту, когда какое-то другое событие (согласование)
        пытается выставить более ранний по прогресс-бару статус.

        "Вперёд"/"назад" зависят от типа оплаты - например, при оплате после
        получения "Оплачено" идёт ПОСЛЕ "На складе", а при полной предоплате
        - до него, поэтому порядок для сравнения берём из поля, отражающего
        именно выбранный self.payment_type, а не из общего списка значений."""
        self.ensure_one()
        if not target_state:
            return
        field_name = {
            'split_50_50': 'state_split_50_50',
            'post_payment': 'state_post_payment',
        }.get(self.payment_type, 'state_full_prepay')
        order_sequence = [key for key, _label in self.env['purchase.request']._fields[field_name].selection]
        target_index = order_sequence.index(target_state)
        # sudo() - это контролируемый переход (кто мог его вызвать, уже
        # проверено видимостью кнопки/группой роли выше по стеку), а не
        # свободное редактирование чужой заявки - права на запись в неё у
        # бухгалтера/кладовщика/главного закупщика по отдельности заводить
        # не нужно.
        for request in requests:
            if target_index >= order_sequence.index(request.state):
                request.sudo().state = target_state

    def _sync_request_state(self):
        for order in self:
            if order.approval_state == 'to_approve':
                order._advance_request_state(order.request_ids, 'to_approve')
            elif order.approval_state == 'approved':
                # Дальше - до оплаты/предоплаты - при разных типах оплаты
                # следующий шаг разный (предоплата/оплата/получение), его
                # определит соответствующее событие само (платёжка/доставка).
                order._advance_request_state(order.request_ids, 'approved')
            elif order.approval_state == 'declined':
                # Отклонение - это осознанный откат назад (нужно доработать
                # и отправить заново), поэтому пишем статус напрямую, минуя
                # проверку "только вперёд".
                order.request_ids.write({'state': 'invoice_generated'})

    def action_skip_payment(self):
        # sudo() - бухгалтер не имеет прямого права записи в заказ (только
        # чтение), но кнопка уже ограничена его ролью, так что сама запись
        # безопасна.
        for order in self:
            order.sudo().payment_skipped = True
            order.sudo().message_post(body=_(
                'Загрузка платёжки пропущена вручную - оплата подтверждается вне ИИ-импорта.'))

    def action_open_payment_import_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.payment.import.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_purchase_order_id': self.id,
                'default_request_id': self.request_ids[:1].id,
            },
        }

    def action_view_request(self):
        """Согласующий видит только сам заказ - без ссылки назад ему негде
        проверить исходную заявку с участка (кто просил, отдел, комментарий),
        прежде чем согласовать закупку."""
        self.ensure_one()
        request = self.request_ids[:1]
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.request',
            'res_id': request.id,
            'view_mode': 'form',
            'target': 'current',
        }
