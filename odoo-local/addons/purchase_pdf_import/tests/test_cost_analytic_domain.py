from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCostAnalyticDomain(TransactionCase):
    """"Статья затрат" (cost_analytic_account_id) раньше не имела домена
    вообще и показывала в выпадающем списке в том числе счёта из плана
    "Категория" (001-008: Самозарядные, Винтовки и т.д.) - это отдельная ось
    (направление/тип продукции), не статья затрат, и её вынесли в отдельное
    поле "Категория" - оставшийся дубль в "Статья затрат" только путал.
    """

    def test_category_accounts_excluded_from_cost_article_domain(self):
        order = self.env['purchase.order'].new({})
        domain = order._fields['cost_analytic_account_id']._description_domain(self.env)
        category_accounts = self.env['account.analytic.account'].search([
            ('plan_id', '=', self.env.ref('purchase_pdf_import.analytic_plan_category').id),
        ])
        self.assertTrue(category_accounts, 'в плане "Категория" должны быть счета для проверки')
        selectable = self.env['account.analytic.account'].search(domain)
        self.assertFalse(
            set(category_accounts.ids) & set(selectable.ids),
            '"Статья затрат" не должна предлагать счета из плана "Категория"')

    def test_real_cost_articles_still_selectable(self):
        order = self.env['purchase.order'].new({})
        domain = order._fields['cost_analytic_account_id']._description_domain(self.env)
        non_category_account = self.env['account.analytic.account'].search([
            ('plan_id', '!=', self.env.ref('purchase_pdf_import.analytic_plan_category').id),
        ], limit=1)
        self.assertTrue(non_category_account, 'нужен хотя бы один счёт вне плана "Категория" для проверки')
        selectable = self.env['account.analytic.account'].search(domain)
        self.assertIn(
            non_category_account, selectable,
            'обычные статьи затрат (вне плана "Категория") должны остаться выбираемыми')
