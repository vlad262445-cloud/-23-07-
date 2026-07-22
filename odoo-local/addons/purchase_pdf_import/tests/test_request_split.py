from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRequestSplit(TransactionCase):
    """Разделение заявки на несколько (когда позиции нужно закупать у разных
    поставщиков) - см. wizard/request_split_wizard.py.

    Самая рискованная часть этой фичи - нумерация дочерних заявок (root.name
    + суффикс, а не новый номер из ir.sequence), поэтому она проверяется
    отдельно и подробно: повторное разделение того же родителя, разделение
    уже дочерней заявки (не должно "вложиться", суффикс всё равно от root),
    и оба варианта отказа (нечего переносить / нечего оставлять).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.department = cls.env['purchase.request.department'].create({'name': 'Test Split Dept'})
        cls.requester = cls.env['res.users'].create({
            'name': 'Test Split Requester',
            'login': 'test_split_requester',
            'email': 'test_split_requester@example.com',
        })

    def _make_request(self, *line_names):
        return self.env['purchase.request'].create({
            'requested_by': self.requester.id,
            'department_id': self.department.id,
            'line_ids': [(0, 0, {'name': name, 'product_qty': 1}) for name in line_names],
        })

    def _split(self, request, selected_names):
        wizard = self.env['purchase.request.split.wizard'].with_context(
            default_request_id=request.id).create({})
        for line in wizard.line_ids:
            line.selected = line.name in selected_names
        wizard.action_split()
        return self.env['purchase.request'].search(
            [('split_from_request_id', '=', request.root_request_id.id)], order='id desc', limit=1)

    def test_split_moves_lines_and_copies_header(self):
        request = self._make_request('Товар А', 'Товар Б', 'Товар В')
        child = self._split(request, ['Товар Б'])

        self.assertEqual(child.name, '%s-2' % request.name)
        self.assertEqual(child.requested_by, request.requested_by)
        self.assertEqual(child.department_id, request.department_id)
        self.assertEqual(child.line_ids.mapped('name'), ['Товар Б'])
        self.assertEqual(sorted(request.line_ids.mapped('name')), ['Товар А', 'Товар В'])

    def test_second_split_gets_next_suffix(self):
        request = self._make_request('Товар А', 'Товар Б', 'Товар В')
        first_child = self._split(request, ['Товар Б'])
        second_child = self._split(request, ['Товар В'])

        self.assertEqual(first_child.name, '%s-2' % request.name)
        self.assertEqual(second_child.name, '%s-3' % request.name)
        self.assertNotEqual(first_child.id, second_child.id)

    def test_splitting_a_child_stays_attached_to_root(self):
        request = self._make_request('Товар А', 'Товар Б', 'Товар В', 'Товар Г')
        child = self._split(request, ['Товар Б', 'Товар В'])
        grandchild = self._split(child, ['Товар В'])

        # Не "ЗКП...-2-2" - суффикс всегда считается от самой первой заявки.
        self.assertEqual(grandchild.name, '%s-3' % request.name)
        self.assertEqual(grandchild.root_request_id, request)
        self.assertEqual(child.root_request_id, request)
        self.assertEqual(request.related_request_count, 2)
        self.assertEqual(child.related_request_count, 2)
        self.assertEqual(grandchild.related_request_count, 2)

    def test_cannot_split_without_selecting_anything(self):
        request = self._make_request('Товар А', 'Товар Б')
        wizard = self.env['purchase.request.split.wizard'].with_context(
            default_request_id=request.id).create({})
        wizard.line_ids.write({'selected': False})
        with self.assertRaises(UserError):
            wizard.action_split()

    def test_cannot_split_moving_every_line(self):
        request = self._make_request('Товар А', 'Товар Б')
        wizard = self.env['purchase.request.split.wizard'].with_context(
            default_request_id=request.id).create({})
        wizard.line_ids.write({'selected': True})
        with self.assertRaises(UserError):
            wizard.action_split()

    def test_cannot_split_once_ordered(self):
        request = self._make_request('Товар А', 'Товар Б')
        partner = self.env['res.partner'].create({'name': 'Test Split Vendor'})
        request.purchase_order_id = self.env['purchase.order'].create({'partner_id': partner.id})
        with self.assertRaises(UserError):
            request.action_open_split_wizard()
