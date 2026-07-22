import base64
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

from .ai_extraction_utils import (
    TEST_URL_ANTHROPIC,
    call_llm,
    call_llm_vision,
    check_proxy_alive,
    extract_text_from_pdf,
    pdf_is_scanned,
    render_pdf_pages_as_png,
)
from .proxy_utils import build_proxy_url

_logger = logging.getLogger(__name__)

DEFAULT_MODEL = 'claude-haiku-4-5'

VENDOR_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "tax_id": {"type": "string"},
        "street": {"type": "string"},
        "city": {"type": "string"},
        "zip": {"type": "string"},
        "country_code": {"type": "string"},
        "phone": {"type": "string"},
        "email": {"type": "string"},
    },
    "additionalProperties": False,
}

ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor": VENDOR_SCHEMA,
        "invoice_number": {"type": "string"},
        "currency": {"type": "string"},
        "discount_note": {"type": "string"},
        "extraction_warning": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit_price": {"type": "number"},
                    "discount_percent": {"type": "number"},
                    "tax_rate": {"type": "number"},
                    "tax_included": {"type": "boolean"},
                    "uncertain": {"type": "boolean"},
                },
                "required": ["name", "quantity", "unit_price"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}

EXTRACTION_INSTRUCTIONS = (
    "You are extracting structured data from a supplier invoice, delivery note, "
    "or price quote. The layout and language can vary between documents - read "
    "carefully rather than relying on a fixed format. First identify the "
    "vendor/seller company (not the buyer): its full legal name, its tax "
    "identification number (e.g. Russian INN/ИНН, VAT number, EIN - whatever "
    "tax ID is printed), its address split into street, city, and postal/zip "
    "code, its country as an ISO 3166-1 alpha-2 code (e.g. RU, US, DE), and "
    "its phone and email if shown. Leave any vendor field empty if it cannot "
    "be determined - never guess a value. Also identify the invoice/order/"
    "document number, and the currency used as an ISO 4217 3-letter code (e.g. "
    "USD, EUR, RUB) - infer it from symbols like $, €, ₽ if no explicit code is "
    "shown. Then extract every distinct product or line item together with its "
    "quantity and its unit price (excluding tax, as shown in the document). If "
    "a quantity is not stated, use 1. If only a line total is given instead of "
    "a unit price, divide the total by the quantity.\n\n"
    "DISCOUNT vs TAX - these are two different, unrelated numbers. Never let "
    "one influence the other, even if they happen to show the same digits "
    "(e.g. a 20% product discount does NOT mean the VAT rate is 20%).\n\n"
    "Discount: only set a non-zero discount_percent when THIS SPECIFIC LINE "
    "shows its own distinct original (pre-discount) price next to the final "
    "price, or its own explicit discount amount/percentage tied to that line "
    "(e.g. a 'price before discount' / 'списочная цена' column, a struck-"
    "through price, or a per-row '% скидки' + 'Скидка' column pair). In that "
    "case set unit_price to the ORIGINAL price and discount_percent to the "
    "discount rate, computed from the two prices if only amounts are given. "
    "If discount information only appears as a generic order-level note (e.g. "
    "'* с учетом скидки 20%, ваша выгода 910 руб' printed once below the "
    "table, with no distinct original price shown for each line) then the "
    "price already printed in the line is final - use it as unit_price with "
    "discount_percent 0; do not invent an original price by reversing that "
    "note's percentage. Instead, whenever such a generic order-level discount/"
    "savings note exists (whether or not it was usable per-line), copy its "
    "text (or a short paraphrase with the same numbers) into the top-level "
    "discount_note field, e.g. 'Скидка 20%, экономия 910 руб (по данным "
    "поставщика)' - this is purely informational and does not affect any "
    "price. Leave discount_note empty if the document has no such note.\n\n"
    "Tax: determine the VAT/tax rate that applies to the items, if the "
    "document indicates one, and separately determine tax_included - these "
    "are two independent questions, answer them from the document's actual "
    "layout, not from each other.\n\n"
    "To decide tax_included, look at how many total-like summary figures the "
    "document shows at the bottom:\n"
    "- THREE figures in sequence - a net/goods subtotal (e.g. 'Итого'), then "
    "a tax amount (e.g. 'Сумма НДС' / 'Сумма НДС 22%'), then a larger final "
    "amount that equals the first two added together (e.g. 'Всего к "
    "оплате') - means tax is EXCLUDED: the unit_price already printed in the "
    "line is the pre-tax price, and tax is added on top to reach that final "
    "figure. Set tax_included = false.\n"
    "- Only ONE grand total is given, with the tax merely mentioned as a "
    "component of that same figure (e.g. 'Всего к оплате: 3540, включая "
    "НДС 638.36' / 'В том числе НДС: 2 220.92', with no separate smaller "
    "pre-tax subtotal shown anywhere else) - means tax is INCLUDED: the "
    "unit_price already printed in the line already has this tax baked in. "
    "Set tax_included = true.\n\n"
    "To determine the rate: if it's stated directly (a '% НДС' column, or "
    "'в т.ч. НДС 20%'), use that number. If only an absolute tax amount is "
    "given with no explicit percentage, compute it: when tax_included is "
    "false, rate = tax_amount / net_subtotal * 100 (using the actual 'Итого' "
    "net figure as base); when tax_included is true, rate = tax_amount / "
    "(grand_total - tax_amount) * 100. Report the rate as tax_rate - a plain "
    "percentage number such as 20, 10, 0, or 22. Apply the same rate and "
    "inclusion mode to every line unless the document breaks tax down "
    "differently per line. If the document does not mention any VAT/tax at "
    "all (e.g. the seller is not a VAT payer), omit both tax_rate and "
    "tax_included entirely rather than guessing.\n\n"
    "Ignore subtotal/total/tax/shipping summary lines - only return actual "
    "product lines.\n\n"
    "HONESTY ABOUT LEGIBILITY - this matters more than completeness. Some "
    "documents are blurry photos, low-resolution scans, have a stamp or "
    "handwriting covering part of the text, or use abbreviations that are "
    "genuinely ambiguous. When you cannot read a specific value with real "
    "confidence (you are pattern-matching/guessing rather than clearly "
    "seeing the characters), do not silently output your best guess as if "
    "it were certain: set that item's uncertain field to true. This applies "
    "per item - only flag the specific lines you are actually unsure about, "
    "not the whole document just because one field was unclear. "
    "Additionally, if the document as a whole has legibility problems worth "
    "a human knowing about (poor scan quality, illegible stamp over key "
    "numbers, a torn or cut-off corner, etc.), describe it briefly in "
    "extraction_warning, e.g. 'Скан низкого качества, часть названия "
    "товара №1 нечитаема'. Leave extraction_warning empty if the document "
    "was clearly legible throughout."
)

EXTRACTION_PROMPT_STRUCTURED = EXTRACTION_INSTRUCTIONS + "\n\nDocument text:\n{text}"

_VENDOR_SHAPE = (
    '{{"name": "...", "tax_id": "...", "street": "...", "city": "...", '
    '"zip": "...", "country_code": "..", "phone": "...", "email": "..."}}'
)

EXTRACTION_PROMPT_PLAIN = (
    EXTRACTION_INSTRUCTIONS + "\n\n"
    "Respond with ONLY a single JSON object, no markdown code fences and no "
    "extra commentary, in exactly this shape:\n"
    '{{"vendor": ' + _VENDOR_SHAPE + ', "invoice_number": "...", '
    '"currency": "...", "discount_note": "...", "extraction_warning": "...", '
    '"items": [{{"name": "...", "quantity": 0, "unit_price": 0.0, '
    '"discount_percent": 0, "tax_rate": 20, "tax_included": false, '
    '"uncertain": false}}, ...]}}\n\n'
    "Document text:\n{text}"
)

EXTRACTION_PROMPT_STRUCTURED_VISION = (
    EXTRACTION_INSTRUCTIONS + "\n\n"
    "Read the invoice/quote page image(s) attached to this message and extract "
    "the data from them."
)

EXTRACTION_PROMPT_PLAIN_VISION = (
    EXTRACTION_INSTRUCTIONS + "\n\n"
    "Read the invoice/quote page image(s) attached to this message.\n\n"
    "Respond with ONLY a single JSON object, no markdown code fences and no "
    "extra commentary, in exactly this shape:\n"
    '{"vendor": ' + _VENDOR_SHAPE.replace('{{', '{').replace('}}', '}') + ', '
    '"invoice_number": "...", "currency": "...", "discount_note": "...", '
    '"extraction_warning": "...", '
    '"items": [{"name": "...", "quantity": 0, "unit_price": 0.0, '
    '"discount_percent": 0, "tax_rate": 20, "tax_included": false, '
    '"uncertain": false}, ...]}'
)

CURRENCY_SYMBOL_MAP = {
    '$': 'USD',
    '€': 'EUR',
    '£': 'GBP',
    '₽': 'RUB',
    '¥': 'JPY',
    '₴': 'UAH',
    '₸': 'KZT',
}


def _values_differ(v1, v2, tol=0.01):
    if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
        return abs(v1 - v2) > tol
    return (v1 or None) != (v2 or None)


def _merge_with_confidence_check(result_a, result_b):
    """Flag fields where two independent vision extractions disagree.

    Disagreement between two independent reads of the same image is a much
    more reliable "this might be wrong" signal than asking the model to
    self-report its own confidence, which it often doesn't do even when its
    reading visibly varies run to run.
    """
    mismatches = []

    vendor_a = (result_a.get('vendor') or {}).get('name')
    vendor_b = (result_b.get('vendor') or {}).get('name')
    if _values_differ(vendor_a, vendor_b):
        mismatches.append(f"поставщик прочитан по-разному ('{vendor_a}' / '{vendor_b}')")

    for key, label in (('invoice_number', 'номер счёта'), ('currency', 'валюта')):
        va, vb = result_a.get(key), result_b.get(key)
        if _values_differ(va, vb):
            mismatches.append(f"{label} прочитан по-разному ('{va}' / '{vb}')")

    items_a = result_a.get('items') or []
    items_b = result_b.get('items') or []
    if len(items_a) != len(items_b):
        mismatches.append(
            f"количество позиций разошлось ({len(items_a)} / {len(items_b)}) - "
            "проверьте, что все товары попали в заказ"
        )
    else:
        for item_a, item_b in zip(items_a, items_b):
            if any(
                _values_differ(item_a.get(key), item_b.get(key))
                for key in ('name', 'quantity', 'unit_price', 'discount_percent',
                            'tax_rate', 'tax_included')
            ):
                item_a['uncertain'] = True

    if mismatches:
        existing = (result_a.get('extraction_warning') or '').strip()
        note = "Повторное чтение документа дало другой результат: " + '; '.join(mismatches)
        result_a['extraction_warning'] = f"{existing} {note}".strip()

    return result_a


class PurchasePdfImportWizard(models.TransientModel):
    _name = 'purchase.pdf.import.wizard'
    _description = 'Импорт заказа на закупку из PDF (ИИ)'

    pdf_file = fields.Binary(string='PDF поставщика')
    pdf_filename = fields.Char(string='Имя файла')
    vendor_id = fields.Many2one(
        'res.partner', string='Поставщик',
        help="Оставьте пустым, чтобы ИИ сам определил (и при необходимости "
             "создал) поставщика из документа.",
    )
    payment_type = fields.Selection([
        ('full_prepay', 'Полная предоплата'),
        ('split_50_50', '50% предоплата + 50% после получения'),
        ('post_payment', 'Оплата после получения'),
    ], string='Тип оплаты', required=True,
        help='От этого зависит, в каком порядке идут этапы оплаты и '
             'получения в статусе заявки.')
    state = fields.Selection([('draft', 'Черновик'), ('done', 'Готово')], default='draft')
    result_order_id = fields.Many2one('purchase.order', string='Созданный заказ', readonly=True)
    log = fields.Text(string='Журнал', readonly=True)
    request_id = fields.Many2one(
        'purchase.request', string='Запрос КП',
        help="Если визард открыт из запроса КП - после оформления заказа "
             "запрос будет связан с созданным заказом и переведён в статус "
             "'Счёт сформирован'.",
    )

    def _check_can_reimport(self, existing_order):
        """Заказ уже отправлен на согласование ИЛИ уже подтверждён/получен -
        значит, кто-то мог его согласовать или принять по нему товар, и
        молча подменять позиции уже небезопасно.

        approval_state одной проверки недостаточно: если в момент создания
        заказа группа "Главный закупщик" была пуста, approval_line_ids
        остаётся пустым и approval_state так и застревает на 'none'
        навсегда (button_confirm/_check_all_approved требуют непустой
        approval_line_ids, чтобы вообще сдвинуть approval_state) - а сам
        заказ при этом можно подтвердить и провести. Проверяем ещё и
        order.state, чтобы такой заказ тоже не переписывался.

        'declined' - осознанное исключение, не дыра: отклонение - это явный
        отказ, никто ничего не одобрял и не получал по нему товар, и
        _sync_request_state() уже откатывает саму заявку в 'invoice_generated'
        именно для повторной загрузки (см. _apply_decline). Найдено 2026-07-21
        на реальном P00948: отклонённый Главным закупщиком заказ, который
        Ольга потом отменила стандартной кнопкой "Отменить", пытаясь начать
        заново, - без этого исключения "Загрузить счёт повторно" остаётся
        недоступна навсегда, и единственный путь - редактировать заказ
        вручную (как и произошло на P00131, до появления этой самой фичи).
        """
        if existing_order.approval_state not in ('none', 'declined') or existing_order.state in ('purchase', 'done'):
            raise UserError(_(
                "Заказ уже отправлен на согласование или подтверждён - повторно "
                "загрузить счёт больше нельзя, чтобы не подменить данные, "
                "которые кто-то уже мог одобрить или принять."
            ))

    def action_import(self):
        self.ensure_one()
        if not self.pdf_file:
            raise UserError(_(
                "Загрузите PDF счёта - без файла нечего распознавать. Если "
                "ИИ недоступен и счёта пока нет, используйте кнопку "
                "\"Оформить без ИИ\" ниже."
            ))
        if not self.payment_type:
            raise UserError(_(
                "Укажите тип оплаты (полная предоплата / 50 на 50 / после "
                "получения) перед оформлением заказа."
            ))
        icp = self.env['ir.config_parameter'].sudo()
        api_key = icp.get_param('purchase_pdf_import.anthropic_api_key')
        if not api_key:
            raise UserError(_(
                "API-ключ не настроен. Откройте Закупки > Настройки импорта PDF "
                "и укажите API-ключ (а если используете кастомный шлюз вместо "
                "Anthropic напрямую - ещё и Base URL)."
            ))
        model = icp.get_param('purchase_pdf_import.model', DEFAULT_MODEL)
        base_url = icp.get_param('purchase_pdf_import.base_url') or None
        proxy_url = build_proxy_url(
            icp.get_param('purchase_pdf_import.proxy_host'),
            icp.get_param('purchase_pdf_import.proxy_port'),
            icp.get_param('purchase_pdf_import.proxy_login'),
            icp.get_param('purchase_pdf_import.proxy_password'),
        )
        proxy_required = icp.get_param('purchase_pdf_import.proxy_required', '1') == '1'

        if proxy_url and proxy_required:
            proxy_error = check_proxy_alive(proxy_url, base_url or TEST_URL_ANTHROPIC)
            if proxy_error:
                raise UserError(_(
                    "Прокси недоступен, импорт остановлен: %s\n\n"
                    "Запрос к AI API не отправлялся - проверьте настройки "
                    "прокси в Закупки > Настройки импорта PDF."
                ) % proxy_error)

        pdf_bytes = base64.b64decode(self.pdf_file)
        text = extract_text_from_pdf(pdf_bytes)
        # a page that's essentially one big embedded photo may still carry a
        # hidden OCR text layer of unknown quality - never trust that layer,
        # always read scanned pages via vision instead
        use_vision = not text.strip() or pdf_is_scanned(pdf_bytes)

        try:
            if use_vision:
                images_png = render_pdf_pages_as_png(pdf_bytes)
                if not images_png:
                    raise UserError(_("Не удалось прочитать ни одной страницы из этого PDF."))
                # scanned/photographed pages are the case where the model can
                # silently mis-read small or blurry text with full apparent
                # confidence (self-reported "uncertain" flags are not
                # reliable for this) - run vision extraction twice and treat
                # any field the two runs disagree on as unreliable, since
                # disagreement is a real, measurable signal rather than the
                # model's own (often absent) self-assessment
                result_a = call_llm_vision(
                    api_key, model, base_url, images_png,
                    EXTRACTION_PROMPT_PLAIN_VISION, EXTRACTION_PROMPT_STRUCTURED_VISION,
                    ITEM_SCHEMA, proxy_url)
                result_b = call_llm_vision(
                    api_key, model, base_url, images_png,
                    EXTRACTION_PROMPT_PLAIN_VISION, EXTRACTION_PROMPT_STRUCTURED_VISION,
                    ITEM_SCHEMA, proxy_url)
                data = _merge_with_confidence_check(result_a, result_b)
            else:
                prompt_plain = EXTRACTION_PROMPT_PLAIN.format(text=text[:20000])
                prompt_structured = EXTRACTION_PROMPT_STRUCTURED.format(text=text[:20000])
                data = call_llm(
                    api_key, model, base_url, prompt_plain, prompt_structured, ITEM_SCHEMA, proxy_url)
        except UserError:
            raise
        except Exception as exc:
            _logger.exception("LLM API call failed")
            raise UserError(_("Ошибка обращения к AI API: %s") % exc)

        items = data.get('items') or []
        if not items:
            raise UserError(_("ИИ не нашёл ни одной позиции товара в этом документе."))

        vendor_data = data.get('vendor') or {}
        invoice_number = (data.get('invoice_number') or '').strip()
        currency_code = (data.get('currency') or '').strip()
        discount_note = (data.get('discount_note') or '').strip()
        extraction_warning = (data.get('extraction_warning') or '').strip()

        partner = self.vendor_id or self._find_or_create_vendor(vendor_data)
        if not partner:
            raise UserError(_(
                "ИИ не смог определить поставщика по этому документу. "
                "Выберите поставщика вручную и попробуйте снова."
            ))
        currency = self._resolve_currency(currency_code)

        order_lines = []
        uncertain_names = []
        log_lines = [
            _("Поставщик: %s") % partner.name,
            _("Номер счёта/накладной: %s") % (invoice_number or '-'),
            _("Валюта: %s") % (currency.name if currency else _('валюта компании по умолчанию')),
        ]
        if discount_note:
            log_lines.append(_("Скидка (по документу, справочно): %s") % discount_note)
        log_lines.append('---')
        for item in items:
            name = (item.get('name') or '').strip()
            if not name:
                continue
            qty = item.get('quantity') or 1.0
            price = item.get('unit_price') or 0.0
            discount = item.get('discount_percent') or 0.0
            discount = max(0.0, min(100.0, discount))
            tax, tax_note = self._resolve_tax(item.get('tax_rate'), item.get('tax_included'))
            uncertain = bool(item.get('uncertain'))
            product = self._find_or_create_product(name, price)
            line_name = f"⚠ ПРОВЕРЬТЕ ВРУЧНУЮ: {name}" if uncertain else name
            order_lines.append((0, 0, {
                'product_id': product.id,
                'name': line_name,
                'product_qty': qty,
                'price_unit': price,
                'discount': discount,
                'taxes_id': [(6, 0, tax.ids)],
            }))
            if uncertain:
                uncertain_names.append(name)
            line_discount_note = f", скидка={discount}%" if discount else ""
            uncertain_note = ", ⚠ ИИ НЕ УВЕРЕН В ЭТОЙ СТРОКЕ" if uncertain else ""
            log_lines.append(
                f"{name}: кол-во={qty}, цена за ед.={price}{line_discount_note}, "
                f"налог={tax_note}{uncertain_note}"
            )

        if extraction_warning or uncertain_names:
            warning_parts = []
            if extraction_warning:
                warning_parts.append(extraction_warning)
            if uncertain_names:
                warning_parts.append(
                    _("Не уверен в точности данных по позициям: %s") % ', '.join(uncertain_names)
                )
            overall_warning = ' '.join(warning_parts)
            log_lines.insert(0, '')
            log_lines.insert(0, _("⚠ ВНИМАНИЕ - ИИ НЕ УВЕРЕН В ЧАСТИ ДАННЫХ, ПРОВЕРЬТЕ ЗАКАЗ ПЕРЕД ПОДТВЕРЖДЕНИЕМ: %s") % overall_warning)
        else:
            overall_warning = ''

        # Если у заявки уже есть заказ - это повторная загрузка счёта
        # (опечатка в первом счёте, поставщик прислал исправленный документ
        # и т.п.), а не оформление нового заказа. Разрешаем это только пока
        # заказ ещё не отправлен на согласование - иначе кто-то может успеть
        # одобрить старые цифры, пока мы их молча меняем.
        existing_order = self.request_id.purchase_order_id if self.request_id else self.env['purchase.order']
        is_correction = bool(existing_order)
        if is_correction:
            self._check_can_reimport(existing_order)

        order_vals = {
            'partner_id': partner.id,
            'order_line': ([(5, 0, 0)] if is_correction else []) + order_lines,
            'payment_type': self.payment_type,
        }
        if invoice_number:
            order_vals['partner_ref'] = invoice_number
        if currency:
            order_vals['currency_id'] = currency.id

        if is_correction:
            order = existing_order
            if order.state == 'cancel':
                # Реальный случай на P00131: не имея способа скорректировать
                # счёт штатно, Ольга нажала стандартную "Отмена" на заказе,
                # пытаясь начать заново - заказ застрял отменённым с пустыми
                # позициями. Раз отправки на согласование ещё не было
                # (проверено выше), просто возвращаем его в черновик перед
                # тем, как записать новые позиции.
                order.button_draft()
            order.write(order_vals)
        else:
            order = self.env['purchase.order'].create(order_vals)

        self._attach_source_pdf(order, discount_note, overall_warning, is_correction)

        if self.request_id and not is_correction:
            self.request_id.write({
                'purchase_order_id': order.id,
                'state': 'invoice_generated',
            })

        self.write({
            'result_order_id': order.id,
            'state': 'done',
            'log': '\n'.join(log_lines),
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.pdf.import.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_import_manual(self):
        """Оформить заказ без ИИ вообще - на случай, если внешний ИИ-сервис
        недоступен (реальный повод: 2026-07-15, шлюз omniroute несколько
        раз подряд оказывался ненадёжен/недоступен, и заявки физически
        нельзя было оформить). Берёт позиции прямо из заявки (название +
        количество, без распознавания цены) - человек дозаполняет цену и
        проверяет заказ вручную на самом заказе после создания.

        Если у заявки уже есть заказ - работает как та же "коррекция", что
        и в action_import (обновляет позиции существующего заказа вместо
        создания дубля), с той же самой проверкой _check_can_reimport -
        иначе повторное нажатие этой кнопки на заявке с уже согласованным
        заказом молча создавало бы второй, никому не видимый заказ и
        отвязывало от него заявку (найдено ревью 2026-07-15).
        """
        self.ensure_one()
        if not self.vendor_id:
            raise UserError(_(
                "Укажите поставщика - без ИИ его некому определить автоматически."))
        if not self.payment_type:
            raise UserError(_(
                "Укажите тип оплаты (полная предоплата / 50 на 50 / после "
                "получения) перед оформлением заказа."
            ))
        if not self.request_id or not self.request_id.line_ids:
            raise UserError(_(
                "Нет позиций для оформления - в связанной заявке должна быть "
                "хотя бы одна позиция."))

        existing_order = self.request_id.purchase_order_id
        is_correction = bool(existing_order)
        if is_correction:
            self._check_can_reimport(existing_order)

        order_lines = []
        for line in self.request_id.line_ids:
            product = self._find_or_create_product(line.name, 0.0)
            order_lines.append((0, 0, {
                'product_id': product.id,
                'name': line.name,
                'product_qty': line.product_qty or 1.0,
                'price_unit': 0.0,
            }))

        order_vals = {
            'partner_id': self.vendor_id.id,
            'order_line': ([(5, 0, 0)] if is_correction else []) + order_lines,
            'payment_type': self.payment_type,
        }

        if is_correction:
            order = existing_order
            if order.state == 'cancel':
                order.button_draft()
            order.write(order_vals)
        else:
            order = self.env['purchase.order'].create(order_vals)

        order.message_post(body=_(
            "Заказ %s вручную, без ИИ-импорта счёта (ИИ был недоступен). "
            "Цены проставлены как 0 - обязательно заполните их и проверьте "
            "заказ, прежде чем отправлять на согласование."
        ) % (_('скорректирован') if is_correction else _('создан')))

        if not is_correction:
            self.request_id.write({
                'purchase_order_id': order.id,
                'state': 'invoice_generated',
            })

        self.write({
            'result_order_id': order.id,
            'state': 'done',
            'log': _("Заказ оформлен вручную, без ИИ - цены не распознавались, проставьте их на заказе."),
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.pdf.import.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_view_order(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'res_id': self.result_order_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _attach_source_pdf(self, order, discount_note='', warning='', is_correction=False):
        from markupsafe import Markup

        attachment = self.env['ir.attachment'].create({
            'name': self.pdf_filename or 'invoice.pdf',
            'datas': self.pdf_file,
            'res_model': 'purchase.order',
            'res_id': order.id,
            'mimetype': 'application/pdf',
        })
        if is_correction:
            body = Markup("<p>%s</p>") % _(
                "Счёт скорректирован повторным ИИ-импортом - позиции и "
                "поставщик обновлены по новому документу.")
        else:
            body = Markup("<p>%s</p>") % _("Исходный документ импортирован через ИИ-импорт PDF.")
        if discount_note:
            body += Markup("<p>%s</p>") % (
                _("Скидка по документу (справочно, уже учтена в ценах): %s") % discount_note
            )
        if warning:
            body += Markup('<p style="color:#a94442;font-weight:bold;">⚠ %s</p>') % (
                _("ИИ не уверен в части данных - проверьте заказ перед подтверждением: %s") % warning
            )
        order.message_post(body=body, attachment_ids=[attachment.id])

    def _find_or_create_product(self, name, price):
        product = self.env['product.product'].search([('name', '=ilike', name)], limit=1)
        if not product:
            # sudo() - у закупщика от природы только чтение product.product
            # (стандартная группа Odoo Закупка/Пользователь прав на создание
            # товара не даёт), а завести новую позицию из счёта - штатная
            # часть импорта, а не что-то, что нужно давать через ACL отдельно.
            product = self.env['product.product'].sudo().create({
                'name': name,
                'standard_price': price,
                'purchase_ok': True,
                'is_storable': True,
            })
        return product

    def _find_or_create_vendor(self, vendor_data):
        name = (vendor_data.get('name') or '').strip()
        if not name:
            return False
        Partner = self.env['res.partner']
        partner = Partner.search(
            [('name', '=ilike', name), ('supplier_rank', '>', 0)], limit=1)
        if not partner:
            partner = Partner.search([('name', '=ilike', name)], limit=1)

        vals = self._partner_vals_from_ai(vendor_data)

        if partner:
            if not partner.supplier_rank:
                vals['supplier_rank'] = 1
            # only fill in fields the contact doesn't already have - never
            # overwrite data that was entered manually
            vals = {k: v for k, v in vals.items() if v and not partner[k]}
            if vals:
                # sudo() - см. _find_or_create_product: не у каждой роли,
                # которой разрешено оформлять заказ, есть отдельное право
                # редактировать контакты, а дозаполнение реквизитов из
                # счёта - штатная часть импорта.
                partner.sudo().write(vals)
            return partner

        vals.update({
            'name': name,
            'company_type': 'company',
            'supplier_rank': 1,
        })
        # sudo() - см. _find_or_create_product: заведение нового поставщика
        # из счёта - штатная часть импорта, а не то, что нужно давать через
        # ACL отдельно каждой роли (Асадуллин, Главный закупщик, упёрся в
        # "Ошибка доступа" на res.partner при первом заказе с новым
        # поставщиком).
        return Partner.sudo().create(vals)

    def _partner_vals_from_ai(self, vendor_data):
        vals = {}
        for field, key in (
            ('vat', 'tax_id'),
            ('street', 'street'),
            ('city', 'city'),
            ('zip', 'zip'),
            ('phone', 'phone'),
            ('email', 'email'),
        ):
            value = (vendor_data.get(key) or '').strip()
            if value:
                vals[field] = value

        country_code = (vendor_data.get('country_code') or '').strip().upper()
        if len(country_code) == 2:
            country = self.env['res.country'].search([('code', '=', country_code)], limit=1)
            if country:
                vals['country_id'] = country.id

        return vals

    def _get_or_create_zero_tax(self):
        domain = [
            ('company_id', '=', self.env.company.id),
            ('type_tax_use', '=', 'purchase'),
            ('amount', '=', 0),
        ]
        tax = self.env['account.tax'].search(domain, limit=1)
        if tax:
            return tax
        return self.env['account.tax'].create({
            'name': 'Без НДС (0%)',
            'amount': 0,
            'amount_type': 'percent',
            'type_tax_use': 'purchase',
            'company_id': self.env.company.id,
        })

    def _resolve_tax(self, tax_rate, tax_included):
        if tax_rate is None:
            # document gives no tax information at all - per company policy
            # default to an explicit 0% tax rather than leaving the line
            # without any tax at all
            return self._get_or_create_zero_tax(), '0% (ставка не указана в документе, применено по умолчанию)'

        domain = [
            ('company_id', '=', self.env.company.id),
            ('type_tax_use', '=', 'purchase'),
        ]
        mode_label = 'в т.ч.' if tax_included else 'сверху'
        if tax_rate:
            # a 0% tax has no meaningful included/excluded distinction - only
            # filter by inclusion mode for non-zero rates
            mode = 'tax_included' if tax_included else 'tax_excluded'
            domain.append(('price_include_override', '=', mode))

        taxes = self.env['account.tax'].search(domain)
        # tolerance of 0.5pp absorbs rounding noise from rates the AI derives
        # from an absolute tax amount (e.g. 22.01 instead of exactly 22) while
        # staying well clear of any two genuinely distinct configured rates
        tax = taxes.filtered(lambda t: abs(t.amount - tax_rate) < 0.5)[:1]
        label = f"{tax_rate}%" + (f" ({mode_label})" if tax_rate else "")
        if tax:
            return tax, label
        return self.env['account.tax'], f"{label} - такая ставка не настроена, налог не проставлен"

    def _resolve_currency(self, code):
        if not code:
            return False
        code = code.strip().upper()
        code = CURRENCY_SYMBOL_MAP.get(code, code)
        if len(code) != 3:
            return False
        return self.env['res.currency'].search([('name', '=', code)], limit=1)
