{
    'name': 'Поиск по номенклатуре в заявках на закупку',
    'version': '18.0.1.0.0',
    'category': 'Purchases',
    'summary': (
        'Поиск по названию/коду/спецификации/примечанию позиций в "Заявках '
        'на закупку", как уже сделано в реестре закупок, плюс фильтры '
        '"Без заказа" и "Просрочена желаемая дата"'
    ),
    'depends': ['purchase_pdf_import'],
    'data': [
        'views/purchase_request_search_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
