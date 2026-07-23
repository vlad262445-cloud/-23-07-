{
    'name': 'Финансовые дашборды',
    'version': '18.0.1.0.0',
    'category': 'Purchases',
    'summary': (
        'Отчётная модель purchase.finance.report (SQL-view, одна строка на '
        'заказ) и 4 ролевых дашборда: Собственник, Генеральный директор, '
        'Финансист, Закупщик - kanban/graph/pivot поверх стандартных '
        'вьюх Odoo'
    ),
    'depends': ['purchase_pdf_import', 'purchase_stock'],
    'data': [
        'security/purchase_finance_dashboard_security.xml',
        'security/ir.model.access.csv',
        'views/purchase_finance_report_views.xml',
        'views/role_dashboards_views.xml',
        'views/res_users_roles_matrix_views.xml',
        'views/dashboard_menus.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
    'post_init_hook': 'assign_known_role_holders',
}
