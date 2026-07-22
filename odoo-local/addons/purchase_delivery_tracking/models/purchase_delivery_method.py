from odoo import fields, models


class PurchaseDeliveryMethod(models.Model):
    _name = 'purchase.delivery.method'
    _description = 'Способ доставки'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    has_tracking = fields.Boolean(
        string='Предполагает трек-номер', default=True,
        help='Подсказка интерфейсу: скрывает (но не стирает) поле трек-номера '
             'там, где номера в принципе не бывает - "Самовывоз", "Силами '
             'поставщика".')
    active = fields.Boolean(default=True)

    def name_create(self, name):
        # Быстрое создание из поля ("Яндекс", "яндекс доставка", "Yandex")
        # не должно плодить дубли при повторном вводе того же названия -
        # см. п. 5.1/5.4 ТЗ. Стандартный name_create в Odoo дублей не
        # проверяет вообще, поэтому ищем существующий способ первым делом.
        existing = self.search([('name', '=ilike', name)], limit=1)
        if existing:
            return existing.id, existing.display_name
        return super().name_create(name)
