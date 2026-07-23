from odoo import api, fields, models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    # "Возраст на этапе" (п. 7.6, экран 2) требует знать, когда заказ ПОСЛЕДНИЙ
    # РАЗ сменил lifecycle_stage - этой истории нигде не было (lifecycle_stage
    # сам - обычное compute+store поле purchase_registry_ux, без tracking).
    # lifecycle_stage_marker хранит значение с предыдущего пересчёта именно
    # для сравнения "было/стало"; lifecycle_stage_since обновляется, только
    # когда значение реально изменилось - тот же "sticky"-приём, что уже
    # использован для completed_date/approval_date в других модулях, но
    # применённый к полю, которое может менять значение много раз за жизнь
    # заказа, а не один раз навсегда. См. NOTES.md.
    #
    # lifecycle_stage - мягкая зависимость от purchase_registry_ux (модуль
    # может быть не установлен, п. 11.1 ТЗ) - обе колонки в этом случае
    # остаются пустыми, days_in_stage = 0.
    lifecycle_stage_marker = fields.Char(copy=False, compute='_compute_lifecycle_stage_since', store=True)
    lifecycle_stage_since = fields.Datetime(copy=False, compute='_compute_lifecycle_stage_since', store=True)
    days_in_stage = fields.Integer(compute='_compute_days_in_stage')

    @api.depends(
        'state', 'approval_state', 'request_state', 'payment_type',
        'amount_paid', 'amount_total', 'document_status')
    def _compute_lifecycle_stage_since(self):
        has_lifecycle_stage = 'lifecycle_stage' in self._fields
        if not has_lifecycle_stage:
            for order in self:
                order.lifecycle_stage_marker = False
                order.lifecycle_stage_since = False
            return
        # Читаем предыдущее сохранённое значение НАПРЯМУЮ из базы одним
        # запросом - не через order.lifecycle_stage_marker (тот же самый
        # compute сейчас его пересчитывает, чтение "себя же" внутри
        # собственного compute небезопасно и может дать неверный или
        # рекурсивно инвалидированный результат).
        real_ids = [order.id for order in self if isinstance(order.id, int)]
        previous = {}
        if real_ids:
            self.env.cr.execute(
                'SELECT id, lifecycle_stage_marker, lifecycle_stage_since '
                'FROM purchase_order WHERE id IN %s', (tuple(real_ids),))
            previous = {row[0]: (row[1], row[2]) for row in self.env.cr.fetchall()}
        now = fields.Datetime.now()
        for order in self:
            current = order.lifecycle_stage
            prev_marker, prev_since = previous.get(order.id, (None, None))
            if current != prev_marker:
                order.lifecycle_stage_marker = current
                order.lifecycle_stage_since = now
            else:
                order.lifecycle_stage_marker = prev_marker
                order.lifecycle_stage_since = prev_since

    def _compute_days_in_stage(self):
        now = fields.Datetime.now()
        for order in self:
            if not order.lifecycle_stage_since:
                order.days_in_stage = 0
                continue
            order.days_in_stage = (now - order.lifecycle_stage_since).days
