import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Порог "оплачено полностью" - тот же 0.95, что уже используется в
# _payment_target_state_raw базового модуля (см. п. 4.1 ТЗ - не выдумываем
# новый порог, переиспользуем существующий).
FULL_PAYMENT_RATIO = 0.95
ARCHIVE_AFTER_WORKING_DAYS = 5


def _add_working_days_fallback(start, days):
    """Пн-пт, без учёта праздников - фолбэк на случай, если у компании не
    задан resource.calendar (см. п. 4.2 ТЗ)."""
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    is_completed = fields.Boolean(compute='_compute_is_completed', store=True)
    completed_date = fields.Datetime(copy=False)
    is_archived = fields.Boolean(default=False, copy=False, index=True)
    archived_date = fields.Datetime(copy=False)

    @api.depends(
        'state', 'approval_state', 'request_state', 'payment_type',
        'amount_paid', 'amount_total', 'document_status')
    def _compute_is_completed(self):
        for order in self:
            order.is_completed = bool(
                order.state != 'cancel'
                and order.approval_state != 'declined'
                and order.amount_total
                and order.amount_paid >= order.amount_total * FULL_PAYMENT_RATIO
                and order.document_status == 'done'
                and order._request_state_reached('in_stock')
            )
            # Выставляется один раз и не сбрасывается, если условие потом
            # временно перестанет выполняться (например, к уже готовому
            # заказу добавили ещё один платёж, который на момент пересчёта
            # ещё не сверен по ИНН, и document_status ненадолго уходит в
            # 'blocked') - иначе счётчик 5 рабочих дней бесконечно
            # перезапускался бы (см. п. 4.1 ТЗ).
            if order.is_completed and not order.completed_date:
                order.completed_date = fields.Datetime.now()

    def action_archive_manually(self):
        for order in self:
            order.write({'is_archived': True, 'archived_date': fields.Datetime.now()})
            order.message_post(body=_('Закупка перемещена в архив вручную.'))

    def action_unarchive(self):
        for order in self:
            order.write({'is_archived': False, 'archived_date': False})
            order.message_post(body=_(
                'Закупка возвращена из архива пользователем %s.') % self.env.user.name)

    def _cron_archive_completed_orders(self):
        orders = self.search([
            ('is_completed', '=', True),
            ('is_archived', '=', False),
            ('completed_date', '!=', False),
        ])
        now = fields.Datetime.now()
        for order in orders:
            calendar = order.company_id.resource_calendar_id
            if calendar:
                deadline = calendar.plan_days(
                    ARCHIVE_AFTER_WORKING_DAYS, order.completed_date, compute_leaves=True)
            else:
                _logger.warning(
                    "У компании %s не задан рабочий календарь - архивация закупки %s "
                    "считается по пн-пт без учёта праздников.", order.company_id.name, order.name)
                deadline = _add_working_days_fallback(
                    order.completed_date, ARCHIVE_AFTER_WORKING_DAYS)
            if now >= deadline:
                order.write({'is_archived': True, 'archived_date': now})
                order.message_post(body=_('Закупка автоматически перемещена в архив.'))
