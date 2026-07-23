{
    'name': 'Карточка поставщика',
    'version': '18.0.1.0.0',
    'category': 'Purchases',
    'summary': (
        'Финансовая картина по поставщику на карточке res.partner - выставлено/'
        'оплачено/едет/должны, вкладки "Что покупаем"/"Надёжность"/"Реквизиты", '
        'список поставщиков, поиск дублей по ИНН/названию'
    ),
    'depends': ['purchase_pdf_import', 'purchase_stock'],
    'data': [
        'views/res_partner_views.xml',
        'views/vendor_list_views.xml',
        'views/vendor_duplicate_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
