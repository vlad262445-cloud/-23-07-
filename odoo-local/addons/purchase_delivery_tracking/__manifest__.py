{
    'name': 'Способ доставки и трек-номер',
    'version': '18.0.1.0.0',
    'category': 'Purchases',
    'summary': (
        'Способ доставки (справочник) и необязательный трек-номер/примечание '
        'на заказе, оформляются через wizard вместо голого confirm='
    ),
    'depends': ['purchase_pdf_import'],
    'data': [
        'security/ir.model.access.csv',
        'data/purchase_delivery_method_data.xml',
        'wizard/delivery_tracking_wizard_views.xml',
        'views/purchase_order_views.xml',
        'views/purchase_request_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
