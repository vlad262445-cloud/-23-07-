{
    'name': 'Архив завершённых закупок',
    'version': '18.0.1.0.0',
    'category': 'Purchases',
    'summary': (
        'Статус "Закупка завершена" и автоматическая архивация через 5 '
        'рабочих дней - архивные закупки не засоряют реестр, но доступны '
        'по требованию'
    ),
    'depends': ['purchase_pdf_import', 'resource'],
    'data': [
        'data/ir_cron_data.xml',
        'views/purchase_order_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
