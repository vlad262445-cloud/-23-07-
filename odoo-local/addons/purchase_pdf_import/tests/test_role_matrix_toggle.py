from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRolesMatrixToggle(TransactionCase):
    """Экран "Роли сотрудников" - новый UI поверх уже существующих полей
    (models/res_users.py, is_buyer и т.п.), без новой логики. Единственное,
    что реально меняется по сравнению с прежним использованием (по одному
    пользователю через Preferences) - запись теперь идёт батчем по
    нескольким пользователям сразу (построчное сохранение в списке). Этот
    тест проверяет именно это: что batch write() на нескольких
    res.users по-прежнему переключает groups_id независимо у каждого."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.buyer_group = cls.env.ref('purchase.group_purchase_user')
        group_user = cls.env.ref('base.group_user')
        cls.user_a = cls.env['res.users'].create({
            'name': 'test_matrix_a',
            'login': 'test_matrix_a',
            'email': 'test_matrix_a@example.com',
            'groups_id': [(6, 0, [group_user.id])],
        })
        cls.user_b = cls.env['res.users'].create({
            'name': 'test_matrix_b',
            'login': 'test_matrix_b',
            'email': 'test_matrix_b@example.com',
            'groups_id': [(6, 0, [group_user.id])],
        })

    def test_bulk_write_toggles_each_user_independently(self):
        (self.user_a + self.user_b).write({'is_buyer': True})
        self.assertIn(self.buyer_group, self.user_a.groups_id)
        self.assertIn(self.buyer_group, self.user_b.groups_id)

        self.user_a.write({'is_buyer': False})
        self.assertNotIn(self.buyer_group, self.user_a.groups_id)
        self.assertIn(self.buyer_group, self.user_b.groups_id)
