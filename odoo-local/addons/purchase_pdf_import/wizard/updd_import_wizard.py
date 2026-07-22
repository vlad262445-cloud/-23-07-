import base64
import json
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
from .pdf_import_wizard import DEFAULT_MODEL, _values_differ
from .proxy_utils import build_proxy_url

_logger = logging.getLogger(__name__)

UPDD_SCHEMA = {
    "type": "object",
    "properties": {
        "updd_number": {"type": "string"},
        "updd_date": {"type": "string"},
        "seller_name": {"type": "string"},
        "seller_inn": {"type": "string"},
        "total_amount": {"type": "number"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": "number"},
                },
                "required": ["name", "quantity"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["total_amount"],
    "additionalProperties": False,
}

UPDD_EXTRACTION_INSTRUCTIONS = (
    "You are extracting structured data from a Russian УПД (Универсальный "
    "передаточный документ - a combined invoice/delivery-acceptance "
    "document) confirming goods have been transferred from seller to "
    "buyer. Extract: the УПД's own document number and date exactly as "
    "printed, the seller's (продавец) company name and ИНН taken from the "
    "seller's requisites block (not the buyer's), the grand total amount "
    "payable as a plain number (no currency symbol or thousands "
    "separators, use the total including VAT if both are shown), and the "
    "list of goods/items with their name (exactly as printed) and "
    "quantity. If the amount is not clearly legible, do not guess: omit it "
    "entirely rather than reporting an uncertain number as fact."
)

UPDD_PROMPT_STRUCTURED = UPDD_EXTRACTION_INSTRUCTIONS + "\n\nDocument text:\n{text}"

UPDD_PROMPT_PLAIN = (
    UPDD_EXTRACTION_INSTRUCTIONS + "\n\n"
    "Respond with ONLY a single JSON object, no markdown code fences and no "
    "extra commentary, in exactly this shape:\n"
    '{{"updd_number": "...", "updd_date": "...", "seller_name": "...", '
    '"seller_inn": "...", "total_amount": 0.0, "items": '
    '[{{"name": "...", "quantity": 0.0}}]}}\n\n'
    "Document text:\n{text}"
)

UPDD_PROMPT_STRUCTURED_VISION = (
    UPDD_EXTRACTION_INSTRUCTIONS + "\n\n"
    "Read the УПД image(s) attached to this message and extract the data from them."
)

UPDD_PROMPT_PLAIN_VISION = (
    UPDD_EXTRACTION_INSTRUCTIONS + "\n\n"
    "Read the УПД image(s) attached to this message.\n\n"
    "Respond with ONLY a single JSON object, no markdown code fences and no "
    "extra commentary, in exactly this shape:\n"
    '{"updd_number": "...", "updd_date": "...", "seller_name": "...", '
    '"seller_inn": "...", "total_amount": 0.0, "items": '
    '[{"name": "...", "quantity": 0.0}]}'
)

MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "matched_order_name": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low", "none"]},
        "reasoning": {"type": "string"},
    },
    "required": ["confidence"],
    "additionalProperties": False,
}

def _merge_updd_confidence_check(result_a, result_b):
    """Flag fields where two independent vision reads of the same УПД
    disagree - see _merge_with_confidence_check in pdf_import_wizard.py for
    why this matters (a scanned page can be misread with full apparent
    confidence, and updd_import_wizard used to only read it once, unlike the
    invoice importer - found 2026-07-15 on УПД №58, where a single read
    fully invented the seller name/ИНН/document number)."""
    mismatches = []
    for key, label in (
        ('seller_name', 'продавец'), ('seller_inn', 'ИНН продавца'),
        ('updd_number', 'номер УПД'), ('updd_date', 'дата УПД'),
        ('total_amount', 'сумма'),
    ):
        if _values_differ(result_a.get(key), result_b.get(key)):
            mismatches.append(f"{label} прочитан(а) по-разному ('{result_a.get(key)}' / '{result_b.get(key)}')")

    items_a = result_a.get('items') or []
    items_b = result_b.get('items') or []
    if len(items_a) != len(items_b):
        mismatches.append(
            f"количество позиций разошлось ({len(items_a)} / {len(items_b)})")
    else:
        for item_a, item_b in zip(items_a, items_b):
            if any(_values_differ(item_a.get(k), item_b.get(k)) for k in ('name', 'quantity')):
                mismatches.append(f"позиция прочитана по-разному ('{item_a.get('name')}' / '{item_b.get('name')}')")

    if mismatches:
        # UPDD_SCHEMA пока не даёт модели своего extraction_warning (в
        # отличие от ITEM_SCHEMA у счёта) - append, а не перезапись, чтобы
        # не потерять его молча, если это поле когда-нибудь добавят и сюда
        # (см. _merge_with_confidence_check в pdf_import_wizard.py - тот же
        # паттерн, ради согласованности, а не задним числом дублирования).
        existing = (result_a.get('extraction_warning') or '').strip()
        note = 'Повторное чтение документа дало другой результат: ' + '; '.join(mismatches)
        result_a['extraction_warning'] = f"{existing} {note}".strip()
    return result_a


MATCH_INSTRUCTIONS = (
    "You are matching a delivery document (УПД) to the purchase order it "
    "belongs to. You are given the list of items from the УПД, and a list "
    "of candidate open purchase orders, each with its own item list (as "
    "originally recognized from that order's vendor invoice). Item names "
    "for the SAME physical product often differ in wording/abbreviation "
    "between the УПД and the original invoice (e.g. 'Метчик М6х1.0' vs "
    "'Метчик гаечный M 6 x 1,0') - match by meaning, not exact text. Pick "
    "the single best-matching candidate order by comparing how many items "
    "correspond and how closely quantities line up. If no candidate is a "
    "plausible match, return confidence 'none' and omit matched_order_name."
)


class PurchaseUpddImportWizard(models.TransientModel):
    _name = 'purchase.updd.import.wizard'
    _description = 'Распознавание УПД (ИИ)'

    pdf_file = fields.Binary(string='УПД', required=True)
    pdf_filename = fields.Char(string='Имя файла')
    purchase_order_id = fields.Many2one(
        'purchase.order', string='Заказ на закупку',
        help='Оставьте пустым, чтобы ИИ сам нашёл подходящий открытый заказ по составу товаров. '
             'Если заказа нет вообще (товар пришёл в обход обычного процесса) - тоже оставьте '
             'пустым: приёмка будет создана и проведена напрямую по товарам из УПД.')
    state = fields.Selection(
        [('draft', 'Черновик'), ('recognized', 'Распознано'), ('done', 'Подтверждено')],
        default='draft',
    )
    recognized_amount = fields.Float(string='Распознанная сумма', readonly=True)
    recognized_date = fields.Char(string='Дата УПД (по документу)', readonly=True)
    recognized_number = fields.Char(string='Номер УПД', readonly=True)
    recognized_seller_inn = fields.Char(string='ИНН продавца (по документу)', readonly=True)
    recognized_seller_name = fields.Char(string='Продавец (по документу)', readonly=True)
    recognized_items_json = fields.Text(string='Распознанные позиции (служебное)')
    expected_amount = fields.Float(string='Сумма по заказу', readonly=True)
    partner_expected_inn = fields.Char(string='ИНН поставщика по заказу', readonly=True)
    inn_mismatch = fields.Boolean(string='ИНН не совпадает', readonly=True)
    match_confidence = fields.Selection([
        ('high', 'Высокая'), ('medium', 'Средняя'), ('low', 'Низкая'), ('none', 'Не найдено'),
    ], string='Уверенность в подборе заказа', readonly=True)
    match_note = fields.Char(string='Комментарий ИИ по подбору заказа', readonly=True)
    extraction_warning = fields.Char(string='Предупреждение о распознавании', readonly=True)
    line_ids = fields.One2many(
        'purchase.updd.import.wizard.line', 'wizard_id', string='Товары и места складирования')

    def _get_llm_settings(self):
        icp = self.env['ir.config_parameter'].sudo()
        api_key = icp.get_param('purchase_pdf_import.anthropic_api_key')
        if not api_key:
            raise UserError(_(
                "API-ключ не настроен. Откройте Закупки > Настройки импорта PDF "
                "и укажите API-ключ."
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
                    "Прокси недоступен, распознавание остановлено: %s"
                ) % proxy_error)
        return api_key, model, base_url, proxy_url

    def action_import(self):
        self.ensure_one()
        api_key, model, base_url, proxy_url = self._get_llm_settings()

        pdf_bytes = base64.b64decode(self.pdf_file)
        text = extract_text_from_pdf(pdf_bytes)
        use_vision = not text.strip() or pdf_is_scanned(pdf_bytes)

        extraction_warning = ''
        try:
            if use_vision:
                images_png = render_pdf_pages_as_png(pdf_bytes)
                if not images_png:
                    raise UserError(_("Не удалось прочитать ни одной страницы из этого PDF."))
                # Скан-фото - модель может уверенно ошибиться в мелком
                # тексте (см. _merge_updd_confidence_check) - читаем дважды
                # и сверяем, как и в обычном импорте счёта.
                result_a = call_llm_vision(
                    api_key, model, base_url, images_png,
                    UPDD_PROMPT_PLAIN_VISION, UPDD_PROMPT_STRUCTURED_VISION,
                    UPDD_SCHEMA, proxy_url)
                result_b = call_llm_vision(
                    api_key, model, base_url, images_png,
                    UPDD_PROMPT_PLAIN_VISION, UPDD_PROMPT_STRUCTURED_VISION,
                    UPDD_SCHEMA, proxy_url)
                data = _merge_updd_confidence_check(result_a, result_b)
                extraction_warning = data.pop('extraction_warning', '')
            else:
                prompt_plain = UPDD_PROMPT_PLAIN.format(text=text[:20000])
                prompt_structured = UPDD_PROMPT_STRUCTURED.format(text=text[:20000])
                data = call_llm(
                    api_key, model, base_url, prompt_plain, prompt_structured,
                    UPDD_SCHEMA, proxy_url)
        except UserError:
            raise
        except Exception as exc:
            _logger.exception("LLM API call failed")
            raise UserError(_("Ошибка обращения к AI API: %s") % exc)

        if data.get('total_amount') is None:
            raise UserError(_("ИИ не смог распознать сумму в этом документе."))

        items = data.get('items') or []
        seller_inn = (data.get('seller_inn') or '').strip()

        order = self.purchase_order_id
        match_confidence = False
        match_note = ''
        if not order and items:
            order, match_confidence, match_note = self._match_order(
                items, seller_inn, api_key, model, base_url, proxy_url)

        partner_inn = (order.partner_id.vat or '').strip() if order else ''
        inn_mismatch = bool(seller_inn) and bool(partner_inn) and seller_inn != partner_inn

        self.write({
            'purchase_order_id': order.id if order else False,
            'recognized_amount': data.get('total_amount') or 0.0,
            'recognized_date': data.get('updd_date') or '',
            'recognized_number': data.get('updd_number') or '',
            'recognized_seller_inn': seller_inn,
            'recognized_seller_name': data.get('seller_name') or '',
            'recognized_items_json': json.dumps(items),
            'expected_amount': order.amount_total if order else 0.0,
            'partner_expected_inn': partner_inn,
            'inn_mismatch': inn_mismatch,
            'match_confidence': match_confidence,
            'match_note': match_note,
            'extraction_warning': extraction_warning,
            'state': 'recognized',
        })
        if order:
            self.action_refresh_lines()
        elif items:
            # Подходящего заказа нет вообще (товар пришёл в обход обычного
            # процесса) - показываем позиции для предпросмотра уже сейчас,
            # чтобы человек видел, что именно оприходуется, до нажатия
            # "Подтвердить УПД". Товары заводятся в каталог здесь же (как и
            # при обычном импорте счёта) - это ещё не сама приёмка, только
            # предпросмотр.
            self._prepare_lines_without_order(items)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.updd.import.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _match_order(self, items, seller_inn, api_key, model, base_url, proxy_url):
        """Ищет открытый заказ, к которому относится этот УПД, сравнивая
        список товаров через ИИ (названия могут отличаться от изначального
        счёта, поэтому сравниваем по смыслу, а не текстом).

        sudo() - поиск кандидатов идёт по всем заказам компании независимо
        от того, что конкретно видно текущему пользователю (например,
        кладовщику не открыт полный список заказов с суммами) - подбирает
        система, а не сам человек."""
        candidates = self.env['purchase.order'].sudo().search([
            ('state', 'in', ('purchase', 'done')),
            ('updd_line_ids', '=', False),
        ], limit=30)
        if seller_inn:
            by_inn = candidates.filtered(lambda o: (o.partner_id.vat or '').strip() == seller_inn)
            if by_inn:
                candidates = by_inn
        if not candidates:
            return self.env['purchase.order'], 'none', _('Нет открытых заказов без УПД для сравнения.')

        items_text = '\n'.join(f"- {it.get('name')} x{it.get('quantity')}" for it in items if it.get('name'))
        candidates_text_parts = []
        for order in candidates:
            lines = '; '.join(order.order_line.mapped('name'))
            candidates_text_parts.append(f"Заказ {order.name} (поставщик {order.partner_id.name}): {lines}")
        candidates_text = '\n'.join(candidates_text_parts)

        prompt = (
            MATCH_INSTRUCTIONS + "\n\n"
            f"Товары по УПД:\n{items_text}\n\n"
            f"Кандидаты (открытые заказы):\n{candidates_text}\n\n"
            "Respond with ONLY a single JSON object: "
            '{"matched_order_name": "...", "confidence": "high|medium|low|none", "reasoning": "..."}'
        )
        try:
            result = call_llm(api_key, model, base_url, prompt, prompt, MATCH_SCHEMA, proxy_url)
        except Exception as exc:
            _logger.exception("LLM order-matching call failed")
            return self.env['purchase.order'], 'none', _('Ошибка при подборе заказа: %s') % exc

        confidence = result.get('confidence') or 'none'
        reasoning = result.get('reasoning') or ''
        matched_name = (result.get('matched_order_name') or '').strip()
        matched_order = candidates.filtered(lambda o: o.name == matched_name)[:1]
        if not matched_order:
            confidence = 'none'
        return matched_order, confidence, reasoning

    def _suggest_location(self, product):
        quant = self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ], order='quantity desc', limit=1)
        return quant.location_id

    def action_refresh_lines(self):
        """Пересчитывает список товаров и подсказки по ячейкам для текущего
        purchase_order_id - вызывается после подбора заказа ИИ, а также
        вручную, если пользователь поменял заказ на другой.

        Явно возвращает действие "открыть эту же форму" в конце - иначе в
        диалоговом окне пустой ответ кнопки трактуется Odoo как "закрыть
        окно", и после клика форма неожиданно закрывалась."""
        self.ensure_one()
        self.line_ids.unlink()
        order = self.purchase_order_id
        if order:
            lines_vals = []
            for order_line in order.order_line:
                product = order_line.product_id
                if not product:
                    continue
                location = self._suggest_location(product)
                lines_vals.append((0, 0, {
                    'product_id': product.id,
                    'quantity': order_line.product_qty,
                    'suggested_location_id': location.id if location else False,
                }))
            self.line_ids = lines_vals

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.updd.import.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _prepare_lines_without_order(self, items):
        """Готовит предпросмотр товаров, когда заказа для сопоставления нет
        - позиции заводятся в каталог сразу (как и при обычном импорте
        счёта), чтобы человек видел реальные записи и мог поправить место
        складирования до нажатия "Подтвердить УПД"."""
        self.line_ids.unlink()
        lines_vals = []
        for item in items:
            name = (item.get('name') or '').strip()
            if not name:
                continue
            product = self._find_or_create_product(name)
            location = self._suggest_location(product)
            lines_vals.append((0, 0, {
                'product_id': product.id,
                'quantity': item.get('quantity') or 1.0,
                'suggested_location_id': location.id if location else False,
            }))
        self.line_ids = lines_vals

    def _find_or_create_product(self, name):
        product = self.env['product.product'].search([('name', '=ilike', name)], limit=1)
        if not product:
            # sudo() - см. пояснение в мастере импорта счёта: у кладовщика от
            # природы только чтение product.product, а завести новый товар
            # из документа - штатная часть импорта.
            product = self.env['product.product'].sudo().create({
                'name': name,
                'purchase_ok': True,
                'is_storable': True,
            })
        return product

    def _find_or_create_vendor(self, name, vat):
        name = (name or '').strip()
        if not name:
            return self.env['res.partner']
        Partner = self.env['res.partner']
        partner = Partner.search([('name', '=ilike', name), ('supplier_rank', '>', 0)], limit=1)
        if not partner:
            partner = Partner.search([('name', '=ilike', name)], limit=1)
        if partner:
            vals = {}
            if not partner.supplier_rank:
                vals['supplier_rank'] = 1
            if vat and not partner.vat:
                vals['vat'] = vat
            if vals:
                partner.sudo().write(vals)
            return partner
        # sudo() - см. пояснение про product.product выше, то же самое для
        # контрагента: у кладовщика нет права создавать res.partner.
        return Partner.sudo().create({
            'name': name,
            'vat': vat or False,
            'company_type': 'company',
            'supplier_rank': 1,
        })

    def action_confirm(self):
        """Человек подтверждает распознанный ИИ УПД - см. пояснение в мастере
        платёжек про то, что ИИ только предлагает значения, ничего не
        применяется автоматически."""
        self.ensure_one()
        if self.purchase_order_id:
            self._confirm_with_order(self.purchase_order_id)
        else:
            if not self.line_ids:
                raise UserError(_(
                    "Нет ни одной позиции для приёмки - проверьте, что ИИ "
                    "распознал товары в этом документе."))
            self._confirm_without_order()
        self.write({'state': 'done'})
        return {'type': 'ir.actions.act_window_close'}

    def _confirm_with_order(self, order):
        # sudo() - прикрепление файла к заказу проверяет право на запись в
        # purchase.order, а у кладовщика/бухгалтера есть только чтение этой
        # модели (нужное им действие уже ограничено кнопкой/группой мастера).
        attachment = self.env['ir.attachment'].sudo().create({
            'name': self.pdf_filename or 'updd.pdf',
            'datas': self.pdf_file,
            'res_model': order._name,
            'res_id': order.id,
            'mimetype': 'application/pdf',
        })

        seller_inn = self.recognized_seller_inn or ''
        partner_inn = self.partner_expected_inn or ''
        can_compare = bool(seller_inn) and bool(partner_inn)
        partner_matched = can_compare and seller_inn == partner_inn

        self.env['purchase.updd.line'].create({
            'purchase_order_id': order.id,
            'updd_date': self.recognized_date,
            'updd_number': self.recognized_number,
            'amount': self.recognized_amount,
            'seller_name': self.recognized_seller_name,
            'seller_inn': seller_inn,
            'partner_matched': partner_matched,
            'attachment_id': attachment.id,
        })

        self._apply_location_suggestions(order)
        self._validate_incoming_pickings(order)

        base_message = _("УПД подтверждён (сумма %s, номер %s).") % (
            self.recognized_amount, self.recognized_number or 'без номера')
        if can_compare and not partner_matched:
            message = base_message + '\n' + _(
                "ВНИМАНИЕ: ИНН продавца по документу (%s) не совпадает с "
                "ИНН поставщика по заказу (%s) - проверьте документ."
            ) % (seller_inn, partner_inn)
        elif not can_compare:
            message = base_message + '\n' + _(
                "Не удалось сверить ИНН продавца с поставщиком (недостаточно данных)."
            )
        else:
            message = base_message

        order.sudo().message_post(body=message, attachment_ids=[attachment.id])
        if can_compare and not partner_matched:
            order.sudo().activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_("Проверить УПД - ИНН продавца не совпадает с ИНН поставщика"),
                note=_("ИНН по документу: %s. ИНН поставщика по заказу: %s.") % (
                    seller_inn, partner_inn),
                user_id=(order.user_id.id or self.env.user.id),
            )

    def _confirm_without_order(self):
        """Товар пришёл в обход обычного процесса - подходящего заказа нет
        и не будет. Вместо того чтобы блокировать кладовщика, сами заводим
        и сразу проводим приёмку по составу УПД (см. согласованный подход:
        человек не должен вручную оформлять это отдельно)."""
        partner = self._find_or_create_vendor(
            self.recognized_seller_name, self.recognized_seller_inn)
        picking = self._create_standalone_picking(partner)

        # sudo() - см. пояснение выше про кладовщика и purchase.order;
        # здесь то же самое, но для stock.picking.
        attachment = self.env['ir.attachment'].sudo().create({
            'name': self.pdf_filename or 'updd.pdf',
            'datas': self.pdf_file,
            'res_model': picking._name,
            'res_id': picking.id,
            'mimetype': 'application/pdf',
        })

        self.env['purchase.updd.line'].create({
            'picking_id': picking.id,
            'updd_date': self.recognized_date,
            'updd_number': self.recognized_number,
            'amount': self.recognized_amount,
            'seller_name': self.recognized_seller_name,
            'seller_inn': self.recognized_seller_inn or '',
            'partner_matched': False,
            'attachment_id': attachment.id,
        })

        message = _(
            "УПД подтверждён без привязки к заказу (сумма %s, номер %s) - "
            "приёмка %s создана и проведена напрямую по составу документа."
        ) % (self.recognized_amount, self.recognized_number or 'без номера', picking.name)
        picking.sudo().message_post(body=message, attachment_ids=[attachment.id])

    def _create_standalone_picking(self, partner):
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'incoming'),
            ('warehouse_id.company_id', '=', self.env.company.id),
        ], limit=1)
        if not picking_type:
            raise UserError(_(
                "Не найден тип складской операции 'Поступления' - обратитесь к администратору."))

        # sudo() - у кладовщика нет прямого права на создание stock.picking
        # без заказа (обычный путь всегда шёл через заказ на закупку), а
        # приёмка без заказа - штатная часть этого мастера.
        picking = self.env['stock.picking'].sudo().create({
            'partner_id': partner.id if partner else False,
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'origin': _('УПД %s (без заказа)') % (self.recognized_number or ''),
            'move_ids': [(0, 0, {
                'name': line.product_id.name,
                'product_id': line.product_id.id,
                'product_uom_qty': line.quantity,
                'product_uom': line.product_id.uom_id.id,
                'location_id': picking_type.default_location_src_id.id,
                'location_dest_id': (
                    line.suggested_location_id.id or picking_type.default_location_dest_id.id),
            }) for line in self.line_ids],
        })
        picking.action_confirm()
        picking.action_assign()
        for move_line in picking.move_line_ids:
            move_line.quantity = move_line.move_id.product_uom_qty
        picking.button_validate()
        return picking

    def _apply_location_suggestions(self, order):
        """Проставляет выбранное/предложенное место складирования в строки
        перемещения приёмки этого заказа - чтобы не заводить новую ячейку
        для товара, который уже где-то лежит."""
        pickings = self.env['stock.picking'].search([
            ('source_purchase_order_id', '=', order.id),
            ('picking_type_id.code', '=', 'incoming'),
        ])
        move_lines = pickings.mapped('move_line_ids')
        for line in self.line_ids:
            if not line.suggested_location_id:
                continue
            target_move_lines = move_lines.filtered(lambda ml: ml.product_id == line.product_id)
            if target_move_lines:
                target_move_lines.write({'location_dest_id': line.suggested_location_id.id})

    def _validate_incoming_pickings(self, order):
        """Подтверждение УПД в этом мастере означает, что товар по заказу
        физически принят - человек уже указал места хранения в этом же окне,
        поэтому закрываем приёмку целиком сразу, а не оставляем её висеть
        в "Резервировано" до отдельного ручного шага Validate."""
        pickings = self.env['stock.picking'].search([
            ('source_purchase_order_id', '=', order.id),
            ('picking_type_id.code', '=', 'incoming'),
            ('state', 'not in', ('done', 'cancel')),
        ])
        for picking in pickings:
            result = picking.button_validate()
            if isinstance(result, dict):
                # Частичная поставка/бэкордер - Odoo просит уточнить это
                # вручную, такое решение должен принять человек, а не мастер.
                continue


class PurchaseUpddImportWizardLine(models.TransientModel):
    _name = 'purchase.updd.import.wizard.line'
    _description = 'Товар из УПД с подсказкой места складирования'

    wizard_id = fields.Many2one('purchase.updd.import.wizard', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Товар', required=True)
    quantity = fields.Float(string='Количество')
    suggested_location_id = fields.Many2one(
        'stock.location', string='Куда положить',
        help='Если товар уже где-то лежит на складе - подставлена та же ячейка, '
             'чтобы не заводить для него новую. Можно выбрать другую вручную.')
