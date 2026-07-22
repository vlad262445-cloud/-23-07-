from datetime import date, timedelta

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRequestSearchViewInherit(TransactionCase):
    """Наш xpath действительно попал в объединённый arch - если бы position
    не нашла узел, install уже упал бы, но здесь ещё и проверяем, что
    видимые элементы - именно те, что мы добавили, а не потерялись при
    последующем обновлении базового модуля."""

    def test_new_elements_present_in_merged_arch(self):
        view = self.env.ref('purchase_pdf_import.view_purchase_request_search')
        result = self.env['purchase.request'].get_view(view_id=view.id, view_type='search')
        arch = result['arch']
        self.assertIn('name="line_ids"', arch)
        self.assertIn('name="filter_no_order"', arch)
        self.assertIn('name="filter_desired_date_overdue"', arch)
        self.assertIn('name="group_by_requested_by"', arch)


@tagged('post_install', '-at_install')
class TestRequestSearchFilters(TransactionCase):
    """Проверяет сами домены, прописанные в filter_domain/domain search-view,
    напрямую через search() - независимо от того, как их потом собирает
    панель поиска в браузере."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.req_by_name = cls.env['purchase.request'].create({
            'line_ids': [(0, 0, {'name': 'Резец токарный MTR-5', 'product_qty': 2})],
        })
        cls.req_by_code = cls.env['purchase.request'].create({
            'line_ids': [(0, 0, {'name': 'Деталь без опознавательных знаков',
                                  'product_code': 'ABC-12345', 'product_qty': 1})],
        })
        cls.req_by_spec = cls.env['purchase.request'].create({
            'line_ids': [(0, 0, {'name': 'Болт', 'spec': 'ГОСТ 7798-70', 'product_qty': 10})],
        })
        cls.req_by_note = cls.env['purchase.request'].create({
            'line_ids': [(0, 0, {'name': 'Прочее', 'note': 'нужно срочно для участка сборки',
                                  'product_qty': 1})],
        })

    def _nomenclature_domain(self, term):
        return ['|', '|', '|',
                ('line_ids.name', 'ilike', term),
                ('line_ids.product_code', 'ilike', term),
                ('line_ids.spec', 'ilike', term),
                ('line_ids.note', 'ilike', term)]

    def test_search_matches_by_line_name(self):
        found = self.env['purchase.request'].search(self._nomenclature_domain('MTR-5'))
        self.assertIn(self.req_by_name, found)

    def test_search_matches_by_product_code(self):
        found = self.env['purchase.request'].search(self._nomenclature_domain('ABC-12345'))
        self.assertIn(self.req_by_code, found)

    def test_search_matches_by_spec(self):
        found = self.env['purchase.request'].search(self._nomenclature_domain('7798-70'))
        self.assertIn(self.req_by_spec, found)

    def test_search_matches_by_note(self):
        found = self.env['purchase.request'].search(self._nomenclature_domain('участка сборки'))
        self.assertIn(self.req_by_note, found)

    def test_search_no_match_for_unrelated_term(self):
        found = self.env['purchase.request'].search(
            self._nomenclature_domain('совершенно несуществующий текст xyz'))
        self.assertFalse(set(found.ids) & {
            self.req_by_name.id, self.req_by_code.id, self.req_by_spec.id, self.req_by_note.id,
        })

    def test_filter_no_order(self):
        with_order = self.env['purchase.request'].create({
            'line_ids': [(0, 0, {'name': 'С заказом', 'product_qty': 1})],
        })
        partner = self.env['res.partner'].create({'name': 'Тестовый поставщик для фильтра'})
        order = self.env['purchase.order'].create({'partner_id': partner.id})
        with_order.purchase_order_id = order.id

        without_order = self.env['purchase.request'].search([('purchase_order_id', '=', False)])
        self.assertIn(self.req_by_name, without_order)
        self.assertNotIn(with_order, without_order)

    def test_filter_desired_date_overdue_excludes_in_stock_and_merged(self):
        yesterday = (date.today() - timedelta(days=1)).strftime('%Y-%m-%d')
        overdue_open = self.env['purchase.request'].create({
            'desired_date': yesterday,
            'line_ids': [(0, 0, {'name': 'Просрочено, но открыто', 'product_qty': 1})],
        })
        overdue_but_in_stock = self.env['purchase.request'].create({
            'desired_date': yesterday, 'state': 'in_stock',
            'line_ids': [(0, 0, {'name': 'Просрочено, но уже на складе', 'product_qty': 1})],
        })
        not_overdue = self.env['purchase.request'].create({
            'desired_date': (date.today() + timedelta(days=7)).strftime('%Y-%m-%d'),
            'line_ids': [(0, 0, {'name': 'Срок ещё не наступил', 'product_qty': 1})],
        })

        domain = [
            ('desired_date', '<', date.today().strftime('%Y-%m-%d')),
            ('state', 'not in', ['in_stock', 'merged']),
        ]
        found = self.env['purchase.request'].search(domain)
        self.assertIn(overdue_open, found)
        self.assertNotIn(overdue_but_in_stock, found, 'заявка на складе не должна считаться просроченной')
        self.assertNotIn(not_overdue, found)

    def test_group_by_requested_by(self):
        user_a = self.env.ref('base.user_admin')
        request = self.env['purchase.request'].create({
            'requested_by': user_a.id,
            'line_ids': [(0, 0, {'name': 'Группировка по заявителю', 'product_qty': 1})],
        })
        groups = self.env['purchase.request']._read_group(
            domain=[('id', '=', request.id)],
            groupby=['requested_by'],
            aggregates=['__count'],
        )
        self.assertTrue(groups)
        requester, count = groups[0]
        self.assertEqual(requester, user_a)
        self.assertEqual(count, 1)
