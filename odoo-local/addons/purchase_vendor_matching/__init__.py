from . import wizard

from .wizard.inn_utils import normalize_inn


def normalize_existing_vendor_inns(env):
    """Разовая нормализация ИНН у уже существующих карточек - см. п. 9.2 ТЗ.
    Идемпотентна: повторный запуск (переустановка модуля) ничего не портит,
    т.к. у уже нормализованных значений normalize_inn(vat) == vat."""
    partners = env['res.partner'].search([('vat', '!=', False)])
    for partner in partners:
        normalized = normalize_inn(partner.vat)
        if normalized and normalized != partner.vat:
            partner.vat = normalized
