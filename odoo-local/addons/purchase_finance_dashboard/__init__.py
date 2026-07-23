from . import models


def assign_known_role_holders(env):
    """Разово проставляет известных носителей ролей (п. 7.2 ТЗ) по login -
    если базу разворачивают заново без этих пользователей, установка не
    должна падать (см. проверку на существование ниже)."""
    role_logins = {
        'purchase_finance_dashboard.group_owner': 'fedchin@odoo',
        'purchase_finance_dashboard.group_ceo': 'gibieva@odoo',
    }
    for xml_id, login in role_logins.items():
        user = env['res.users'].search([('login', '=', login)], limit=1)
        if user:
            user.groups_id = [(4, env.ref(xml_id).id)]
