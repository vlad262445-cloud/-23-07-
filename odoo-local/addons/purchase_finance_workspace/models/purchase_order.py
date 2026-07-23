from odoo import _, api, fields, models

FINANCE_STAGES = [
    ('wait_approval', 'Ждёт согласования'),
    ('to_pay', 'Требуется оплата'),
    ('to_pay_urgent', 'Срочная оплата'),
    ('to_surcharge', 'Требуется доплата'),
    ('to_upload_slip', 'Загрузить платёжку'),
    ('inn_mismatch', 'Сверить ИНН'),
    ('done', 'Закрыто'),
]
# Приоритет при нескольких совпадениях (п. 6.1 ТЗ) - зафиксирован здесь
# отдельной константой и покрыт тестом (test_finance_stage_priority.py),
# а не разбросан по цепочке if/elif.
FINANCE_STAGE_PRIORITY = [
    'inn_mismatch', 'to_pay_urgent', 'to_surcharge', 'to_pay', 'to_upload_slip', 'done', 'wait_approval',
]
FULL_PAYMENT_RATIO = 0.95
# Маршруты, где деньги по смыслу процесса уходят ДО отгрузки - именно для
# них имеет смысл "требуется оплата"/"срочная оплата" на этом раннем шаге.
# post_payment платит ПОСЛЕ получения - для него до самого факта получения
# финансисту действовать ещё рано (см. NOTES.md).
PREPAYMENT_ROUTES = ('full_prepay', 'split_50_50')


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    # --- 6.1: вычисляемое поле-маршрутизатор -------------------------------
    finance_stage = fields.Selection(
        FINANCE_STAGES, compute='_compute_finance_stage', store=True, index=True)
    # Момент первого перехода в approval_state == 'approved' - фиксируется
    # один раз и не сбрасывается (тот же приём, что completed_date в
    # purchase_order_archive) - нужен для сортировки days_waiting_payment,
    # который сам не stored (см. п. 6.3 ТЗ).
    approval_date = fields.Datetime(copy=False)

    @api.depends(
        'approval_state', 'payment_type', 'amount_paid', 'amount_total', 'payment_priority',
        'payment_line_ids.partner_matched', 'payment_skipped', 'request_state')
    def _compute_finance_stage(self):
        for order in self:
            if order.approval_state == 'approved' and not order.approval_date:
                order.approval_date = fields.Datetime.now()
            order.finance_stage = order._finance_stage_key()

    def _finance_stage_matches(self):
        """Множество ключей, чьё условие сейчас выполняется - может
        выполняться несколько одновременно (например, to_pay и
        to_pay_urgent), окончательный выбор - в _finance_stage_key() по
        FINANCE_STAGE_PRIORITY."""
        self.ensure_one()
        matches = set()
        if self.approval_state in ('none', 'to_approve'):
            matches.add('wait_approval')
            return matches
        # approval_state == 'approved' дальше
        if self.payment_type in PREPAYMENT_ROUTES and self.amount_paid == 0:
            matches.add('to_pay')
            if self.payment_priority in ('1', '2'):
                matches.add('to_pay_urgent')
        if self.amount_total and 0 < self.amount_paid < self.amount_total * FULL_PAYMENT_RATIO:
            matches.add('to_surcharge')
        if self._request_state_reached('invoice_paid') and (
                not self.payment_line_ids or self.payment_skipped):
            matches.add('to_upload_slip')
        if self.payment_line_ids.filtered(lambda line: not line.partner_matched):
            matches.add('inn_mismatch')
        if (self.amount_total and self.amount_paid >= self.amount_total * FULL_PAYMENT_RATIO
                and self.payment_line_ids and not self.payment_skipped):
            matches.add('done')
        if not matches:
            # approved, но ни одно условие не подошло - например,
            # post_payment, который ещё даже не доехал до склада. Для
            # финансиста здесь буквально нечего делать прямо сейчас -
            # переиспользуем wait_approval как "очередь пуста", а не
            # заводим восьмое значение под один этот случай (см. NOTES.md).
            matches.add('wait_approval')
        return matches

    def _finance_stage_key(self):
        self.ensure_one()
        matches = self._finance_stage_matches()
        for key in FINANCE_STAGE_PRIORITY:
            if key in matches:
                return key
        return 'wait_approval'

    # --- 6.3: поля-помощники ------------------------------------------
    amount_residual_purchase = fields.Monetary(
        compute='_compute_amount_residual_purchase', store=True,
        currency_field='currency_id', string='Остаток к оплате')

    @api.depends('amount_total', 'amount_paid')
    def _compute_amount_residual_purchase(self):
        for order in self:
            order.amount_residual_purchase = order.amount_total - order.amount_paid

    payment_due_date = fields.Date(
        compute='_compute_payment_due_date', string='Срок (по заявке)')

    @api.depends('request_ids.desired_date')
    def _compute_payment_due_date(self):
        for order in self:
            order.payment_due_date = order.request_ids[:1].desired_date

    # Не stored (зависит от "сегодня") - для сортировки использовать
    # approval_date (см. п. 6.3 ТЗ).
    days_waiting_payment = fields.Integer(compute='_compute_days_waiting_payment')

    @api.depends('approval_date')
    def _compute_days_waiting_payment(self):
        today = fields.Date.context_today(self)
        for order in self:
            order.days_waiting_payment = (
                (today - order.approval_date.date()).days if order.approval_date else 0)

    # --- 6.2: поставщик (кратко) на карточках/списках финансиста -----------
    # short_name живёт на res.partner, заводится purchase_registry_ux -
    # мягкая зависимость (проверка поля в рантайме, а не в манифесте), этот
    # модуль обязан работать и без него (п. 11.1 ТЗ: "модули 6 и 7
    # используют поля модулей 3-5, но должны устанавливаться и работать без
    # них"). См. NOTES.md.
    finance_partner_display_name = fields.Char(
        compute='_compute_finance_partner_display_name', string='Поставщик')

    @api.depends('partner_id.name')
    def _compute_finance_partner_display_name(self):
        has_short_name = 'short_name' in self.env['res.partner']._fields
        for order in self:
            short_name = has_short_name and order.partner_id.short_name
            order.finance_partner_display_name = short_name or order.partner_id.name
