from . import models
from . import wizard


def seed_default_analytic_account(env):
    if env['account.analytic.account'].search_count([]):
        return
    plan = env['account.analytic.plan'].search([], limit=1)
    if not plan:
        return
    env['account.analytic.account'].create({
        'name': 'Общие расходы',
        'plan_id': plan.id,
    })
    # Встроенный в Odoo порог "двойного согласования" закупок (Настройки >
    # Закупки) - это ОТДЕЛЬНЫЙ от approval_line_ids механизм: он требует
    # purchase.group_purchase_manager, которой нет ни у одной роли в этой
    # системе, чтобы снять заказ со статуса "К согласованию". Собственная
    # система согласования уже полностью решает эту задачу - без отключения
    # порога любой заказ дороже 50 000 (дефолт) молча зависал бы после того,
    # как все согласующие уже одобрили его (обнаружено 2026-07-13 на P00002,
    # 134 000 руб).
    env['res.company'].search([]).write({'po_double_validation': 'one_step'})
