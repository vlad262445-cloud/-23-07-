{
    'name': 'Рабочее место финансиста',
    'version': '18.0.1.0.0',
    'category': 'Purchases',
    'summary': (
        'Отдельное приложение "Финансы" для бухгалтера: kanban по этапам '
        'оплаты, списки "Требуется оплата/Доплата/Загрузить платёжку/'
        'Сверить ИНН", отчёт "Оплачено за период"'
    ),
    'depends': ['purchase_pdf_import'],
    'data': [
        'views/purchase_finance_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
