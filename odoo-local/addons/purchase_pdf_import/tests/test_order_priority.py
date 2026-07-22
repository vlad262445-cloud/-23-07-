from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestOrderPriority(TransactionCase):
    """Приоритет заказа (Обычная/Срочно/Критично, widget="priority") - живёт
    на ЗАКАЗЕ (purchase.order), не на заявке (перенесено 2026-07-17 - изначально
    был на заявке, но это сигнал ВСЕМ, кто работает с уже оформленной закупкой
    с реальной суммой - закупщику, бухгалтеру, кладовщику, - а не то, что
    выглядело срочным ещё на этапе заявки, до появления счёта).

    Отдельное имя поля (payment_priority, не priority) - чтобы не путать со
    штатным полем Odoo purchase.order.priority (2 уровня, 1 звезда), которое
    на форме заказа скрыто и заменено этим (см. purchase_order_priority_views.xml).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group_user = cls.env.ref('base.group_user')
        cls.plain_user = cls.env['res.users'].create({
            'name': 'Test Order Priority Plain User',
            'login': 'test_order_priority_plain_user',
            'email': 'test_order_priority_plain_user@example.com',
            'groups_id': [(6, 0, [group_user.id])],
        })
        cls.buyer = cls.env['res.users'].create({
            'name': 'Test Order Priority Buyer',
            'login': 'test_order_priority_buyer',
            'email': 'test_order_priority_buyer@example.com',
            'groups_id': [(6, 0, [cls.env.ref('purchase.group_purchase_user').id])],
        })
        cls.partner = cls.env['res.partner'].create({'name': 'Test Order Priority Vendor'})

    def _make_order(self):
        return self.env['purchase.order'].create({'partner_id': self.partner.id})

    def test_default_priority_is_normal(self):
        order = self._make_order()
        self.assertEqual(order.payment_priority, '0')

    def test_buyer_can_set_priority(self):
        order = self._make_order()
        order.with_user(self.buyer).write({'payment_priority': '2'})
        self.assertEqual(order.payment_priority, '2')

    def test_plain_user_cannot_set_priority(self):
        # base.group_user и так не имеет права на запись в purchase.order
        # вообще (ACL модельно-широко read-only) - обычный write() уже упал бы
        # на AccessError раньше, чем дойдёт до этой проверки. Вызываем констрейнт
        # напрямую, чтобы проверить именно его логику - это на случай, если
        # в будущем какая-то роль получит write-доступ к заказу не будучи
        # закупщиком/Главным закупщиком (защита в глубину, не единственный рубеж).
        order = self._make_order()
        order.payment_priority = '1'
        with self.assertRaises(ValidationError):
            order.with_user(self.plain_user)._check_payment_priority_setter()
