from odoo import api, fields, models


class PurchaseApprovalLine(models.Model):
    _inherit = 'purchase.approval.line'

    # П. 7.6 ТЗ, экран 3 ("Согласование: кто держит") - decision_date уже
    # существует в базовом модуле, здесь только разница до неё (или до
    # текущего момента, если ещё не решено - чтобы "висящие" строки тоже
    # попадали в среднее/сортировку по возрасту).
    days_to_decide = fields.Float(compute='_compute_days_to_decide')

    @api.depends('create_date', 'decision_date', 'state')
    def _compute_days_to_decide(self):
        now = fields.Datetime.now()
        for line in self:
            if not line.create_date:
                line.days_to_decide = 0.0
                continue
            end = line.decision_date if line.state != 'pending' else now
            line.days_to_decide = (end - line.create_date).total_seconds() / 86400.0
