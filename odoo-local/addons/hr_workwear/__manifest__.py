{
    'name': 'Спецодежда',
    'version': '18.0.1.0.0',
    'category': 'Human Resources',
    'summary': (
        'Личный состав с размерами спецодежды, учёт выдачи, нормы и '
        'напоминания о просрочке, стык с заявками на закупку'
    ),
    'depends': ['hr', 'purchase_pdf_import', 'purchase_stock'],
    'data': [
        'security/hr_workwear_security.xml',
        'security/ir.model.access.csv',
        'data/hr_department_data.xml',
        'data/workwear_type_data.xml',
        'data/workwear_size_data.xml',
        'data/ir_cron_data.xml',
        'views/purchase_request_department_views.xml',
        'views/res_partner_contacts_views.xml',
        'views/hr_employee_views.xml',
        'views/workwear_type_size_views.xml',
        'views/workwear_issue_views.xml',
        'views/workwear_norm_views.xml',
        'views/workwear_requirement_views.xml',
        'views/workwear_overdue_views.xml',
        'report/workwear_reports.xml',
        'report/workwear_employee_card_template.xml',
        'report/workwear_issue_slip_template.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
    'post_init_hook': 'post_init_hook',
}
