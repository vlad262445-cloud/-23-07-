{
    'name': 'Сопоставление поставщиков по ИНН',
    'version': '18.0.1.0.0',
    'category': 'Purchases',
    'summary': (
        'Мастера ИИ-импорта (PDF-счёт, УПД) ищут существующего поставщика '
        'по ИНН с проверкой контрольной суммы, прежде чем сравнивать по '
        'названию - меньше дублей карточек контрагентов'
    ),
    'depends': ['purchase_pdf_import'],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
    'post_init_hook': 'normalize_existing_vendor_inns',
}
