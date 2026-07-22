from odoo import api, fields, models

# XML ID сначала - чтобы не дублировать одну и ту же строку в 5 парах
# compute/inverse ниже.
_ROLE_GROUPS = {
    'is_buyer': 'purchase.group_purchase_user',
    'is_chief_buyer': 'purchase_pdf_import.group_chief_buyer',
    'is_accountant': 'purchase_pdf_import.group_accountant',
    'is_warehouse_keeper': 'purchase_pdf_import.group_warehouse_keeper',
    'is_observer': 'purchase_pdf_import.group_observer',
}


class ResUsers(models.Model):
    _inherit = 'res.users'

    department_id = fields.Many2one(
        'purchase.request.department', string='Отдел/участок',
        help='"Домашний" отдел сотрудника. Если указан, обычный сотрудник '
             '(без роли Закупщика/Главного закупщика) сможет подавать заявки '
             'только по этому отделу - поле "Отдел/участок" в заявке будет '
             'заблокировано на этом значении. Закупщика/менеджера это не '
             'ограничивает.')

    # Технически это членство в res.groups (уже доступно на вкладке "Права
    # доступа"), но там оно теряется среди технических групп всех остальных
    # модулей. Эти 5 галочек - те же самые роли, но в понятном виде рядом с
    # "Отдел/участок", без похода в общие настройки.
    is_buyer = fields.Boolean(string='Закупщик', compute='_compute_purchase_roles', inverse='_inverse_is_buyer')
    is_chief_buyer = fields.Boolean(
        string='Главный закупщик', compute='_compute_purchase_roles', inverse='_inverse_is_chief_buyer')
    is_accountant = fields.Boolean(
        string='Бухгалтер', compute='_compute_purchase_roles', inverse='_inverse_is_accountant')
    is_warehouse_keeper = fields.Boolean(
        string='Кладовщик', compute='_compute_purchase_roles', inverse='_inverse_is_warehouse_keeper')
    is_observer = fields.Boolean(
        string='Наблюдатель', compute='_compute_purchase_roles', inverse='_inverse_is_observer')

    @api.depends('groups_id')
    def _compute_purchase_roles(self):
        groups = {field: self.env.ref(xml_id) for field, xml_id in _ROLE_GROUPS.items()}
        for user in self:
            for field, group in groups.items():
                user[field] = group in user.groups_id

    def _toggle_role_group(self, field):
        # Значения читаются ДО начала цикла записи, а не "user[field]" внутри
        # него - найдено 2026-07-20 при батчевом переключении роли сразу у
        # нескольких пользователей (экран "Роли сотрудников"): запись
        # groups_id для первого юзера в цикле сбрасывала закэшированное
        # значение is_buyer/и т.п. у ЕЩЁ НЕ обработанных юзеров той же
        # рекордсет-пачки (это воспроизводилось даже с @api.depends выше),
        # так что второй и последующие юзеры молча читали своё старое
        # значение вместо только что записанного через write(), и роль им
        # никогда не назначалась. Раньше это не проявлялось, потому что поле
        # всегда переключали по одному пользователю через Preferences.
        group = self.env.ref(_ROLE_GROUPS[field])
        targets = {user.id: user[field] for user in self}
        for user in self:
            user.groups_id = [(4, group.id) if targets[user.id] else (3, group.id)]

    def _inverse_is_buyer(self):
        self._toggle_role_group('is_buyer')

    def _inverse_is_chief_buyer(self):
        self._toggle_role_group('is_chief_buyer')

    def _inverse_is_accountant(self):
        self._toggle_role_group('is_accountant')

    def _inverse_is_warehouse_keeper(self):
        self._toggle_role_group('is_warehouse_keeper')

    def _inverse_is_observer(self):
        self._toggle_role_group('is_observer')
