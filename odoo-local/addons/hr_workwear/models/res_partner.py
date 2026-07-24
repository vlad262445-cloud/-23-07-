from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # Установка hr создаёт res.partner ("рабочий контакт", work_contact_id)
    # на КАЖДОГО hr.employee - это штатное поведение самой Odoo (hr.
    # employee.create() безусловно вызывает _create_work_contacts()),
    # трогать/отключать это небезопасно (переписка/уведомления сотрудника
    # на этом завязаны - как раз тот случай, который просили не ломать).
    # Вместо этого - чисто дополнительная классификация для группировки/
    # фильтрации в общем экране "Контакты", чтобы 66+ рабочих контактов не
    # смешивались визуально с 21 реальным поставщиком и остальными
    # карточками. Ничего не меняет по умолчанию - только новые фильтры/
    # группировка в search view (см. views/res_partner_contacts_views.xml).
    contact_kind = fields.Selection([
        ('employee', 'Сотрудник (рабочий контакт)'),
        ('supplier', 'Поставщик'),
        ('customer', 'Клиент'),
        ('other', 'Прочее'),
    ], compute='_compute_contact_kind', store=True, string='Тип контакта')

    @api.depends('employee_ids', 'supplier_rank', 'customer_rank')
    def _compute_contact_kind(self):
        for partner in self:
            if partner.employee_ids:
                partner.contact_kind = 'employee'
            elif partner.supplier_rank > 0:
                partner.contact_kind = 'supplier'
            elif partner.customer_rank > 0:
                partner.contact_kind = 'customer'
            else:
                partner.contact_kind = 'other'
