from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRequestLock(TransactionCase):
    """Заявка блокируется от правок сама, как только у неё появляется заказ
    (purchase_order_id) - чтобы список позиций не разошёлся молча с уже
    созданным заказом. До этого момента заявку можно свободно редактировать
    (закупщик может ещё только присматриваться/договариваться с поставщиком -
    ничего ещё не куплено). "Разблокировать" снимает блокировку на одно
    следующее сохранение, доступна только заявителю/Главному закупщику -
    после сохранения заявка сама блокируется снова (см. write() в
    purchase_request.py).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group_user = cls.env.ref('base.group_user')
        cls.requester = cls.env['res.users'].create({
            'name': 'Test Lock Requester',
            'login': 'test_lock_requester',
            'email': 'test_lock_requester@example.com',
            'groups_id': [(6, 0, [group_user.id])],
        })
        cls.other_user = cls.env['res.users'].create({
            'name': 'Test Lock Other User',
            'login': 'test_lock_other_user',
            'email': 'test_lock_other_user@example.com',
            'groups_id': [(6, 0, [group_user.id])],
        })
        cls.chief_buyer = cls.env['res.users'].create({
            'name': 'Test Lock Chief Buyer',
            'login': 'test_lock_chief_buyer',
            'email': 'test_lock_chief_buyer@example.com',
            'groups_id': [(6, 0, [cls.env.ref('purchase_pdf_import.group_chief_buyer').id])],
        })
        cls.department = cls.env['purchase.request.department'].create({'name': 'Test Lock Dept'})
        cls.partner = cls.env['res.partner'].create({'name': 'Test Lock Vendor'})

    def _make_request(self, requester=None):
        return self.env['purchase.request'].create({
            'requested_by': (requester or self.requester).id,
            'department_id': self.department.id,
            'line_ids': [(0, 0, {'name': 'Резец МТР-5', 'product_qty': 2})],
        })

    def _give_order(self, request):
        request.purchase_order_id = self.env['purchase.order'].create({'partner_id': self.partner.id})

    def test_editing_before_order_is_free(self):
        request = self._make_request()
        request.write({'cost_code': 'ABC-1', 'desired_date': '2026-08-01'})
        self.assertEqual(request.cost_code, 'ABC-1')

    def test_lock_activates_once_order_is_set(self):
        request = self._make_request()
        self._give_order(request)
        with self.assertRaises(UserError):
            request.write({'cost_code': 'HACK'})
        with self.assertRaises(UserError):
            request.write({'line_ids': [(0, 0, {'name': 'Ещё товар', 'product_qty': 1})]})

    def test_setting_purchase_order_id_itself_is_not_blocked(self):
        # Именно ЭТА запись (появление заказа) не должна попадать под
        # собственную же блокировку - иначе оформить заказ было бы вообще
        # невозможно.
        request = self._make_request()
        self._give_order(request)
        self.assertTrue(request.purchase_order_id)

    def test_system_state_advance_is_not_blocked_by_lock(self):
        # Служебные переходы статуса (например, приёмка на складе) пишут
        # только 'state', не входящий в список заблокированных полей -
        # локальная блокировка их не должна затрагивать вообще.
        request = self._make_request()
        self._give_order(request)
        request.write({'state': 'in_stock'})
        self.assertEqual(request.state, 'in_stock')

    def test_unlock_allows_one_edit_then_relocks(self):
        request = self._make_request()
        self._give_order(request)
        request.with_user(self.requester).action_unlock()
        self.assertTrue(request.is_unlocked)

        request.with_user(self.requester).write({'cost_code': 'CORRECTED'})
        self.assertEqual(request.cost_code, 'CORRECTED')
        self.assertFalse(request.is_unlocked, 'заявка должна снова заблокироваться после сохранения правки')

        with self.assertRaises(UserError):
            request.write({'cost_code': 'HACK-AGAIN'})

    def test_only_requester_or_chief_buyer_can_unlock(self):
        request = self._make_request()
        self._give_order(request)
        with self.assertRaises(UserError):
            request.with_user(self.other_user).action_unlock()

        request.with_user(self.chief_buyer).action_unlock()
        self.assertTrue(request.is_unlocked)

    def test_line_diff_logged_after_unlock_edit(self):
        request = self._make_request()
        self._give_order(request)
        request.with_user(self.requester).action_unlock()
        line = request.line_ids[0]
        request.with_user(self.requester).write({
            'line_ids': [(1, line.id, {'product_qty': 5})],
        })
        messages = request.message_ids.mapped('body')
        self.assertTrue(
            any('Изменения после разблокировки' in body and 'Кол-во' in body for body in messages),
            'изменение количества после разблокировки должно попасть в чаттер')

    def test_no_diff_logged_for_normal_pre_order_edit(self):
        request = self._make_request()
        message_count_before = len(request.message_ids)
        request.write({'cost_code': 'ABC-2'})
        messages = request.message_ids.mapped('body')
        self.assertFalse(
            any('Изменения после разблокировки' in body for body in messages),
            'обычная правка до оформления заказа не должна логироваться как коррекция')
