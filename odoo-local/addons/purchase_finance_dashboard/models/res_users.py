from odoo import api, fields, models

# Не переиспользует _ROLE_GROUPS/_compute_purchase_roles из purchase_pdf_import
# нарочно: тот словарь - обычный модульный Python-объект (не поле модели), и
# мутировать его из чужого модуля означало бы, что при уdaлении
# purchase_finance_dashboard ключи is_ceo/is_owner останутся в словаре
# навсегда (Python не "отменяет" такие мутации при uninstall модуля) - тогда
# следующий пересчёт _compute_purchase_roles попытается env.ref() уже
# удалённые группы и упадёт для ВСЕХ пяти базовых ролей разом, не только
# для этих двух. Отдельные compute/inverse - да, три строки дублирования
# принципа "toggle_role_group", зато полностью независимо от чужого модуля.


class ResUsers(models.Model):
    _inherit = 'res.users'

    is_ceo = fields.Boolean(
        string='Генеральный директор', compute='_compute_ceo_owner_roles', inverse='_inverse_is_ceo')
    is_owner = fields.Boolean(
        string='Собственник', compute='_compute_ceo_owner_roles', inverse='_inverse_is_owner')

    @api.depends('groups_id')
    def _compute_ceo_owner_roles(self):
        ceo_group = self.env.ref('purchase_finance_dashboard.group_ceo')
        owner_group = self.env.ref('purchase_finance_dashboard.group_owner')
        for user in self:
            user.is_ceo = ceo_group in user.groups_id
            user.is_owner = owner_group in user.groups_id

    def _toggle_ceo_owner_group(self, field, xml_id):
        # Тот же приём и то же обнаруженное 2026-07-20 условие гонки, что и
        # в оригинальном _toggle_role_group (см. purchase_pdf_import/models/
        # res_users.py): значения читаются ДО начала цикла записи.
        group = self.env.ref(xml_id)
        targets = {user.id: user[field] for user in self}
        for user in self:
            user.groups_id = [(4, group.id) if targets[user.id] else (3, group.id)]

    def _inverse_is_ceo(self):
        self._toggle_ceo_owner_group('is_ceo', 'purchase_finance_dashboard.group_ceo')

    def _inverse_is_owner(self):
        self._toggle_ceo_owner_group('is_owner', 'purchase_finance_dashboard.group_owner')
