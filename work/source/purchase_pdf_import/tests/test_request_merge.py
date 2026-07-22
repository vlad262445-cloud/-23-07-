from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRequestMerge(TransactionCase):
    """Объединение нескольких заявок в одну (обратная операция к "Разделить
    заявку") - когда мелкая позиция (например, "линейка") сама по себе не
    набирает минимальную сумму заказа поставщика, и её выгоднее объединить
    с другой, не связанной заявкой (в том числе от другого отдела/человека).

    Самое рискованное место здесь - не перенос строк (тот же проверенный
    (4, id), что и в split), а то, что донор после переноса остаётся с 0
    позиций: _check_has_lines должен пропускать именно и только состояние
    'merged', и донор должен получить это состояние РАНЬШЕ, чем у него
    отберут последнюю строку (см. request_merge_wizard.py). Второй важный
    момент - новое ir.rule, дающее автору донора read-only доступ к чужой
    заявке-цели: должно давать именно read, а не read+write (тот же класс
    дыры, что был найден 2026-07-13 у Наблюдателя).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dept_a = cls.env['purchase.request.department'].create({'name': 'Test Merge Dept A'})
        cls.dept_b = cls.env['purchase.request.department'].create({'name': 'Test Merge Dept B'})
        # groups_id указан явно и намеренно ограничен base.group_user - без
        # этого res.users.create() в этом инстансе молча выдаёт
        # Administrator-уровень на Закупки/Склад (см. CLAUDE.md, готча
        # "User create default-groups bug") - тест на реальный ACL-барьер
        # (test_merge_donor_requester_can_read_but_not_write_target) иначе
        # не смог бы отличить "рядовой сотрудник" от "админ по умолчанию".
        group_user = cls.env.ref('base.group_user')
        cls.requester_a = cls.env['res.users'].create({
            'name': 'Test Merge Requester A',
            'login': 'test_merge_requester_a',
            'email': 'test_merge_requester_a@example.com',
            'groups_id': [(6, 0, [group_user.id])],
        })
        cls.requester_b = cls.env['res.users'].create({
            'name': 'Test Merge Requester B',
            'login': 'test_merge_requester_b',
            'email': 'test_merge_requester_b@example.com',
            'groups_id': [(6, 0, [group_user.id])],
        })

    def _make_request(self, requester, department, *line_names):
        return self.env['purchase.request'].create({
            'requested_by': requester.id,
            'department_id': department.id,
            'line_ids': [(0, 0, {'name': name, 'product_qty': 1}) for name in line_names],
        })

    def _merge(self, requests, target):
        wizard = self.env['purchase.request.merge.wizard'].with_context(
            active_ids=requests.ids).create({
                'target_request_id': target.id,
                'reason': 'Тестовая причина объединения',
            })
        wizard.action_merge()

    def test_merge_moves_lines_and_marks_donor_merged(self):
        target = self._make_request(self.requester_a, self.dept_a, 'Товар А')
        donor = self._make_request(self.requester_b, self.dept_b, 'Линейка')

        self._merge(target + donor, target)

        self.assertEqual(donor.state, 'merged')
        self.assertEqual(donor.merged_into_request_id, target)
        self.assertFalse(donor.line_ids, 'у донора не должно остаться своих позиций')
        self.assertEqual(sorted(target.line_ids.mapped('name')), ['Линейка', 'Товар А'])
        self.assertIn(donor, target.merged_donor_ids)
        self.assertEqual(target.merged_donor_count, 1)

    def test_merge_chatter_mentions_transferred_items_and_reason(self):
        # Найдено по итогам обсуждения с пользователем 2026-07-16: номера
        # заявок в чате недостаточно - и закупщик, и автор донора должны
        # понимать, КАКОЙ ИМЕННО товар переехал и ПОЧЕМУ.
        target = self._make_request(self.requester_a, self.dept_a, 'Товар А')
        donor = self._make_request(self.requester_b, self.dept_b, 'Линейка металлическая')

        wizard = self.env['purchase.request.merge.wizard'].with_context(
            active_ids=(target + donor).ids).create({
                'target_request_id': target.id,
                'reason': 'Линейка сама по себе не набирает минимальный заказ поставщика',
            })
        wizard.action_merge()

        donor_body = donor.message_ids[0].body
        self.assertIn('Линейка металлическая', donor_body)
        self.assertIn('не набирает минимальный заказ', donor_body)

        target_body = target.message_ids[0].body
        self.assertIn(donor.name, target_body)
        self.assertIn('Линейка металлическая', target_body)
        self.assertIn('не набирает минимальный заказ', target_body)

    def test_merge_allows_cross_department(self):
        # Смысл фичи именно в этом - объединять мелочи от РАЗНЫХ людей/отделов
        # под одного поставщика, а не только заявки одного человека (как split).
        target = self._make_request(self.requester_a, self.dept_a, 'Товар А')
        donor = self._make_request(self.requester_b, self.dept_b, 'Линейка')
        self.assertNotEqual(target.requested_by, donor.requested_by)
        self.assertNotEqual(target.department_id, donor.department_id)

        self._merge(target + donor, target)

        self.assertEqual(donor.state, 'merged')
        # requested_by/department_id заявки-цели не переписываются - донор
        # просто перестаёт нести свои позиции, провенанс - только в чаттере.
        self.assertEqual(target.requested_by, self.requester_a)

    def test_merge_requires_at_least_two_requests(self):
        target = self._make_request(self.requester_a, self.dept_a, 'Товар А')
        with self.assertRaises(UserError):
            self._merge(target, target)

    def test_merge_target_must_be_in_selection(self):
        target = self._make_request(self.requester_a, self.dept_a, 'Товар А')
        donor = self._make_request(self.requester_b, self.dept_b, 'Линейка')
        other = self._make_request(self.requester_a, self.dept_a, 'Товар В')
        with self.assertRaises(UserError):
            self._merge(target + donor, other)

    def test_cannot_merge_donor_already_ordered(self):
        target = self._make_request(self.requester_a, self.dept_a, 'Товар А')
        donor = self._make_request(self.requester_b, self.dept_b, 'Линейка')
        partner = self.env['res.partner'].create({'name': 'Test Merge Vendor'})
        donor.purchase_order_id = self.env['purchase.order'].create({'partner_id': partner.id})
        with self.assertRaises(UserError):
            self._merge(target + donor, target)

    def test_cannot_merge_into_already_ordered_target(self):
        target = self._make_request(self.requester_a, self.dept_a, 'Товар А')
        donor = self._make_request(self.requester_b, self.dept_b, 'Линейка')
        partner = self.env['res.partner'].create({'name': 'Test Merge Vendor'})
        target.purchase_order_id = self.env['purchase.order'].create({'partner_id': partner.id})
        with self.assertRaises(UserError):
            self._merge(target + donor, target)

    def test_cannot_remerge_already_merged_donor(self):
        target_1 = self._make_request(self.requester_a, self.dept_a, 'Товар А')
        donor = self._make_request(self.requester_b, self.dept_b, 'Линейка')
        self._merge(target_1 + donor, target_1)

        target_2 = self._make_request(self.requester_a, self.dept_a, 'Товар Б')
        with self.assertRaises(UserError):
            self._merge(target_2 + donor, target_2)

    def test_cannot_merge_into_already_merged_target(self):
        first_target = self._make_request(self.requester_a, self.dept_a, 'Товар А')
        first_donor = self._make_request(self.requester_b, self.dept_b, 'Линейка')
        self._merge(first_target + first_donor, first_target)
        # first_target теперь сам стал донором в другом объединении.
        second_target = self._make_request(self.requester_a, self.dept_a, 'Товар Б')
        self._merge(second_target + first_target, second_target)
        self.assertEqual(first_target.state, 'merged')

        third_donor = self._make_request(self.requester_b, self.dept_b, 'Ещё линейка')
        with self.assertRaises(UserError):
            self._merge(first_target + third_donor, first_target)

    def test_merge_donor_requester_can_read_but_not_write_target(self):
        target = self._make_request(self.requester_a, self.dept_a, 'Товар А')
        donor = self._make_request(self.requester_b, self.dept_b, 'Линейка')
        self._merge(target + donor, target)

        target_as_b = target.with_user(self.requester_b)
        # Не должно падать - у автора донора есть read на заявку-цель.
        target_as_b.read(['name'])
        with self.assertRaises(AccessError):
            target_as_b.write({'cost_code': 'HACK'})

    def test_merging_split_child_leaves_root_family_intact(self):
        root = self._make_request(self.requester_a, self.dept_a, 'Товар А', 'Товар Б')
        split_wizard = self.env['purchase.request.split.wizard'].with_context(
            default_request_id=root.id).create({})
        for line in split_wizard.line_ids:
            line.selected = line.name == 'Товар Б'
        split_wizard.action_split()
        child = self.env['purchase.request'].search(
            [('split_from_request_id', '=', root.id)], order='id desc', limit=1)
        self.assertEqual(root.related_request_count, 1)

        unrelated_donor = self._make_request(self.requester_b, self.dept_b, 'Линейка')
        self._merge(child + unrelated_donor, child)

        self.assertEqual(child.root_request_id, root, 'семья split не должна пострадать от merge')
        self.assertEqual(root.related_request_count, 1)
        self.assertEqual(unrelated_donor.state, 'merged')
