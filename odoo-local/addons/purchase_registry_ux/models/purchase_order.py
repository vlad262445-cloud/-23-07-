from odoo import api, fields, models
from odoo.tools.misc import format_date

# Ключ -> короткая подпись (widget="badge"/строковая колонка). Порядок здесь
# не влияет на выбор - только на порядок значений в выпадающем списке.
PENDING_ACTION_SHORT_LABELS = [
    ('to_approve', 'Согласовать'),
    ('pay', 'Оплатить'),
    ('surcharge', 'Доплатить'),
    ('arrange_delivery', 'Оформить доставку'),
    ('upload_updd', 'Загрузить УПД'),
    ('fill_fields', 'Заполнить поля'),
    ('declined', 'Отклонена'),
    ('none', '—'),
]

LIFECYCLE_STAGES = [
    ('draft', 'Черновик'),
    ('to_approve', 'На согласовании'),
    ('approved', 'Согласовано'),
    ('prepaid', 'Предоплачено'),
    ('in_transit', 'В пути'),
    ('in_stock', 'На складе'),
    ('completed', 'Завершена'),
    ('declined', 'Отклонена'),
    ('cancel', 'Отменена'),
]
LIFECYCLE_PROGRESS = {
    'draft': 10, 'to_approve': 25, 'approved': 40, 'prepaid': 55,
    'in_transit': 70, 'in_stock': 85, 'completed': 100,
    'declined': 0, 'cancel': 0,
}


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    # --- 2.1: полезная нагрузка для раскрывающейся строки реестра --------
    registry_expand_data = fields.Json(compute='_compute_registry_expand_data')

    @api.depends(
        'partner_ref', 'amount_total', 'partner_id.name',
        'cost_analytic_account_id.name', 'cost_category_id.name',
        'expected_arrival_date',
        'order_line.name', 'order_line.product_qty', 'order_line.price_unit',
        'order_line.price_subtotal')
    def _compute_registry_expand_data(self):
        for order in self:
            order.registry_expand_data = {
                'partner_ref': order.partner_ref or '',
                # Полные значения для колонок, сокращённых в п. 2.2 - не
                # нашёл в Odoo 18 подтверждённого widget-опциона вида
                # options="{'tooltip_field': ...}" (см. NOTES.md), поэтому
                # используется прямо разрешённый ТЗ запасной путь - полное
                # значение доступно в этом раскрывающемся блоке.
                'partner_name': order.partner_id.name or '',
                'cost_analytic_name': order.cost_analytic_account_id.name or '',
                'cost_category_name': order.cost_category_id.name or '',
                'expected_arrival_date': (
                    format_date(self.env, order.expected_arrival_date)
                    if order.expected_arrival_date else ''),
                'lines': [{
                    'name': line.name,
                    'qty': line.product_qty,
                    'price': line.price_unit,
                    'subtotal': line.price_subtotal,
                } for line in order.order_line],
                'amount_total': order.amount_total,
                'currency_symbol': order.currency_id.symbol or '',
            }

    # --- 2.1 запасной вариант [Стандарт] - если OWL-компонент сломается на
    # будущем обновлении Odoo, colonка "Позиции" (текст) + partner_ref
    # отдельной колонкой остаются рабочим способом увидеть состав заказа
    # (см. п. 2.1 ТЗ). optional="hide" в реестре.
    order_lines_preview = fields.Char(compute='_compute_order_lines_preview', string='Позиции')

    @api.depends('order_line.name', 'order_line.product_qty')
    def _compute_order_lines_preview(self):
        for order in self:
            order.order_lines_preview = ', '.join(
                f"{line.product_qty:g}× {line.name}" for line in order.order_line if line.name
            )

    # --- 2.2: сокращённые колонки -----------------------------------------
    partner_short_name = fields.Char(related='partner_id.short_name', string='Поставщик')
    cost_analytic_short = fields.Char(
        compute='_compute_cost_short_names', string='Статья затрат (кратко)')
    cost_category_short = fields.Char(
        compute='_compute_cost_short_names', string='Категория (кратко)')

    @api.depends(
        'cost_analytic_account_id.code', 'cost_analytic_account_id.name',
        'cost_category_id.code', 'cost_category_id.name')
    def _compute_cost_short_names(self):
        for order in self:
            order.cost_analytic_short = self._short_analytic_label(order.cost_analytic_account_id)
            order.cost_category_short = self._short_analytic_label(order.cost_category_id)

    @staticmethod
    def _short_analytic_label(account):
        # Если у account.analytic.account заполнен code - показывать code,
        # иначе первые 20 символов имени (п. 2.2 ТЗ).
        if not account:
            return False
        if account.code:
            return account.code
        return (account.name or '')[:20]

    pending_action_short = fields.Selection(
        PENDING_ACTION_SHORT_LABELS, compute='_compute_pending_action_short', store=True)

    @api.depends(
        'state', 'approval_state', 'cost_analytic_account_id', 'cost_category_id',
        'payment_type', 'can_arrange_delivery', 'updd_relevant', 'updd_line_ids',
        'updd_skipped', 'document_status', 'amount_paid', 'amount_total')
    def _compute_pending_action_short(self):
        for order in self:
            order.pending_action_short = order._pending_action_key()

    def _pending_action_key(self):
        """Тот же разбор ситуации, что и в _compute_pending_action_note
        базового модуля (см. purchase_pdf_import/models/purchase_order.py) -
        общий приватный метод, на котором строится короткий ярлык, без
        дублирования формулировок длинного pending_action_note (он остаётся
        как есть, см. п. 2.2 ТЗ). Здесь - только КЛЮЧ, а не текст.

        Разметка веток на 8 значений короче, чем веток в исходном методе -
        несколько исходных случаев осознанно схлопнуты в один ярлык (см.
        NOTES.md за подробным объяснением каждого решения)."""
        self.ensure_one()
        if not isinstance(self.id, int):
            return 'none'
        if self.state == 'cancel':
            return 'none'
        if self.approval_state == 'declined':
            return 'declined'
        if self.approval_state == 'none':
            if not self.cost_analytic_account_id or not self.cost_category_id or not self.payment_type:
                return 'fill_fields'
            return 'to_approve'
        if self.approval_state == 'to_approve':
            return 'to_approve'
        # approval_state == 'approved' дальше
        if self.can_arrange_delivery:
            return 'arrange_delivery'
        if self.updd_relevant and not self.updd_line_ids and not self.updd_skipped:
            return 'upload_updd'
        # Доплата проверяется НЕЗАВИСИМО от document_status: как только
        # появляется хоть один payment_line (пусть и на 40% от суммы),
        # _compute_document_status в базовом модуле уже не считает
        # "платёжка не прикреплена" - она перестаёт быть недостающим
        # документом сама по себе, документ 'blocked' может даже стать
        # 'done'. Полнота суммы document_status вообще не проверяет, только
        # факт наличия платёжки - поэтому "доплатить" нужно ловить по самой
        # сумме, а не по статусу документов.
        if self.amount_total and 0 < self.amount_paid < self.amount_total * 0.95:
            return 'surcharge'
        if self.document_status == 'blocked':
            return 'pay'
        return 'none'

    # --- 2.3: единая шкала статуса ------------------------------------
    lifecycle_stage = fields.Selection(
        LIFECYCLE_STAGES, compute='_compute_lifecycle_stage', store=True)
    lifecycle_progress = fields.Integer(compute='_compute_lifecycle_stage', store=True)

    # Отзыв пользователя 2026-07-23: "должна быть возможность увидеть
    # ориентировочную дату прибытия" рядом со шкалой этапов. На заказе
    # такого поля не было вообще - только "Желательная дата" на заявке
    # (purchase.request.desired_date, то, что изначально попросил
    # заявитель). Тот же паттерн, что уже есть в базовом модуле для
    # requester_id (order.request_ids[:1].requested_by) - берём с первой
    # связанной заявки.
    expected_arrival_date = fields.Date(
        compute='_compute_expected_arrival_date', store=True,
        string='Ориентировочная дата прибытия')

    @api.depends('request_ids.desired_date')
    def _compute_expected_arrival_date(self):
        for order in self:
            order.expected_arrival_date = order.request_ids[:1].desired_date

    # is_completed (из purchase_order_archive) в зависимостях не участвует -
    # его вообще может не быть на модели, если тот модуль не установлен
    # (модули 1-5 внедряются независимо, см. п. 11.1 ТЗ), а @api.depends
    # требует существования полей на момент сборки реестра моделей. Вместо
    # этого - те же самые поля-триггеры, что и у is_completed в
    # purchase_order_archive (state/approval_state/request_state/
    # payment_type/amount_paid/amount_total/document_status): раз
    # is_completed целиком строится из них, синхронный пересчёт
    # lifecycle_stage при каждом их изменении держит обе величины в
    # согласованном состоянии без явной ссылки на чужое поле в декораторе.
    # Подробнее - NOTES.md.
    @api.depends(
        'state', 'approval_state', 'request_state', 'payment_type',
        'amount_paid', 'amount_total', 'document_status')
    def _compute_lifecycle_stage(self):
        has_is_completed = 'is_completed' in self._fields
        for order in self:
            stage = order._lifecycle_stage_key(has_is_completed)
            order.lifecycle_stage = stage
            order.lifecycle_progress = LIFECYCLE_PROGRESS[stage]

    def _lifecycle_stage_key(self, has_is_completed):
        self.ensure_one()
        if self.approval_state == 'declined':
            return 'declined'
        if self.state == 'cancel':
            return 'cancel'
        if has_is_completed and self.is_completed:
            return 'completed'
        if self._request_state_reached('in_stock'):
            return 'in_stock'
        if self.request_state == 'in_transit':
            return 'in_transit'
        if self.amount_paid > 0:
            return 'prepaid'
        if self.approval_state == 'approved':
            return 'approved'
        if self.approval_state == 'to_approve':
            return 'to_approve'
        return 'draft'
