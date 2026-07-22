import re

from odoo import api, fields, models

# Порядок важен: более специфичные формы (ПАО/ЗАО) должны проверяться раньше
# общего "акционерное общество"/"АО", иначе потеряется уточнение. Так же для
# полных названий раньше самих аббревиатур не имеет значения (аббревиатуры
# внутри полных названий не встречаются как отдельное слово).
_LEGAL_FORM_PATTERNS = [
    (re.compile(r'публичное\s+акционерное\s+общество', re.IGNORECASE), 'ПАО'),
    (re.compile(r'\bпао\b', re.IGNORECASE), 'ПАО'),
    (re.compile(r'закрытое\s+акционерное\s+общество', re.IGNORECASE), 'ЗАО'),
    (re.compile(r'\bзао\b', re.IGNORECASE), 'ЗАО'),
    (re.compile(r'акционерное\s+общество', re.IGNORECASE), 'АО'),
    (re.compile(r'\bао\b', re.IGNORECASE), 'АО'),
    (re.compile(r'общество\s+с\s+ограниченной\s+ответственностью', re.IGNORECASE), 'ООО'),
    (re.compile(r'\bооо\b', re.IGNORECASE), 'ООО'),
    (re.compile(r'индивидуальный\s+предприниматель', re.IGNORECASE), 'ИП'),
    (re.compile(r'\bип\b', re.IGNORECASE), 'ИП'),
]
_QUOTE_CHARS = '"\'«'


def build_short_name(name):
    """Правила сокращения из п. 2.2 ТЗ:
    - ООО/Общество с ограниченной ответственностью -> ООО (аналогично АО/ИП);
    - название в кавычках остаётся как есть: ООО "СИЭНСИЭМ Груп";
    - ФИО (три слова после формы, только для ИП) -> Фамилия И.О.;
    - если ни то ни другое - просто аббревиатура + остаток без изменений.
    """
    name = (name or '').strip()
    if not name:
        return ''
    for pattern, abbr in _LEGAL_FORM_PATTERNS:
        match = pattern.search(name)
        if not match:
            continue
        # Не включать кавычки в strip() - иначе закрывающая кавычка тоже
        # обрежется, и проверка "остаток начинается с кавычки" ниже никогда
        # не сработает для "ООО "Название"" (закрывающая кавычка исчезнет).
        remainder = (name[:match.start()] + name[match.end():]).strip(' ,.')
        if not remainder:
            return abbr
        if remainder[0] in _QUOTE_CHARS:
            return f'{abbr} {remainder}'
        if abbr == 'ИП':
            words = remainder.split()
            if len(words) == 3 and all(w[:1].isupper() for w in words):
                surname, first, patronymic = words
                return f'{surname} {first[0]}.{patronymic[0]}.'
        return f'{abbr} {remainder}'
    return name


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # Живёт здесь (владелец поля - purchase_registry_ux), переиспользуется
    # модулем purchase_vendor_card - см. НЕ дублировать поле под другим
    # именем, если тот модуль ставится без этого (п. 2.2 ТЗ).
    short_name = fields.Char(string='Короткое имя')
    short_name_manual = fields.Boolean(
        string='Короткое имя задано вручную', default=False, copy=False,
        help='Ставится автоматически, как только короткое имя правят руками - '
             'дальнейшие автоматические пересчёты (например, при изменении '
             'полного названия) больше его не перетирают.')

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        for partner, vals in zip(partners, vals_list):
            if not vals.get('short_name'):
                # short_name_auto_update - иначе write() ниже примет это за
                # ручную правку и поставит short_name_manual=True на ровном
                # месте, ещё до того как кто-то вообще открыл карточку.
                partner.with_context(short_name_auto_update=True).short_name = \
                    build_short_name(partner.name)
        return partners

    def write(self, vals):
        if 'short_name' in vals and not self.env.context.get('short_name_auto_update'):
            vals = dict(vals, short_name_manual=True)
        result = super().write(vals)
        if 'name' in vals:
            for partner in self:
                if not partner.short_name_manual:
                    partner.with_context(short_name_auto_update=True).short_name = \
                        build_short_name(partner.name)
        return result
