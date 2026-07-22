{
    'name': 'Реестр закупок: разгрузка и ускорение',
    'version': '18.0.1.0.0',
    'category': 'Purchases',
    'summary': (
        'Раскрывающиеся строки вместо модального окна, сокращённые колонки '
        '(поставщик/статья/категория/что требуется), единая шкала '
        'жизненного цикла заказа вместо четырёх статусных колонок'
    ),
    'depends': ['purchase_pdf_import'],
    'data': [
        'views/purchase_registry_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'purchase_registry_ux/static/src/js/registry_list_renderer.js',
            'purchase_registry_ux/static/src/js/lifecycle_steps_field.js',
            'purchase_registry_ux/static/src/xml/registry_list_renderer.xml',
            'purchase_registry_ux/static/src/xml/lifecycle_steps_field.xml',
            'purchase_registry_ux/static/src/scss/registry_ux.scss',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
