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
from .pdf_import_wizard import DEFAULT_MODEL
from .proxy_utils import build_proxy_url

_logger = logging.getLogger(__name__)

PAYMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "amount": {"type": "number"},
        "payment_date": {"type": "string"},
        "purpose": {"type": "string"},
        "payer_name": {"type": "string"},
        "payee_name": {"type": "string"},
        "recipient_inn": {"type": "string"},
        "payment_number": {"type": "string"},
    },
    "required": ["amount"],
    "additionalProperties": False,
}

PAYMENT_EXTRACTION_INSTRUCTIONS = (
    "You are extracting structured data from a bank payment order (платёжное "
    "поручение) confirming a money transfer that already happened. Extract: "
    "the transferred amount as a plain number (no currency symbol or "
    "thousands separators), the payment date exactly as printed (do not "
    "reformat it), the payer's and payee's company names, and the full "
    "'назначение платежа' / purpose-of-payment text verbatim - it often "
    "references an invoice or order number, which is useful for matching "
    "this payment to the right order, so copy it exactly rather than "
    "paraphrasing. The document lists an ИНН for BOTH parties (payer/"
    "плательщик and recipient/получатель) - extract recipient_inn as ONLY "
    "the recipient's (получатель's) ИНН, taken from the recipient's "
    "requisites block, not the payer's. Also extract payment_number - the "
    "payment order's own number (номер платёжного поручения), if printed. "
    "If the amount is not clearly legible, do not guess: omit it entirely "
    "rather than reporting an uncertain number as fact."
)

PAYMENT_PROMPT_STRUCTURED = PAYMENT_EXTRACTION_INSTRUCTIONS + "\n\nDocument text:\n{text}"

PAYMENT_PROMPT_PLAIN = (
    PAYMENT_EXTRACTION_INSTRUCTIONS + "\n\n"
    "Respond with ONLY a single JSON object, no markdown code fences and no "
    "extra commentary, in exactly this shape:\n"
    '{{"amount": 0.0, "payment_date": "...", "purpose": "...", '
    '"payer_name": "...", "payee_name": "...", "recipient_inn": "...", '
    '"payment_number": "..."}}\n\n'
    "Document text:\n{text}"
)

PAYMENT_PROMPT_STRUCTURED_VISION = (
    PAYMENT_EXTRACTION_INSTRUCTIONS + "\n\n"
    "Read the payment order image(s) attached to this message and extract "
    "the data from them."
)

PAYMENT_PROMPT_PLAIN_VISION = (
    PAYMENT_EXTRACTION_INSTRUCTIONS + "\n\n"
    "Read the payment order image(s) attached to this message.\n\n"
    "Respond with ONLY a single JSON object, no markdown code fences and no "
    "extra commentary, in exactly this shape:\n"
    '{"amount": 0.0, "payment_date": "...", "purpose": "...", '
    '"payer_name": "...", "payee_name": "...", "recipient_inn": "...", '
    '"payment_number": "..."}'
)


class PurchasePaymentImportWizard(models.TransientModel):
    _name = 'purchase.payment.import.wizard'
    _description = 'Распознавание платёжки (ИИ)'

    pdf_file = fields.Binary(string='Платёжное поручение', required=True)
    pdf_filename = fields.Char(string='Имя файла')
    request_id = fields.Many2one('purchase.request', string='Запрос КП')
    purchase_order_id = fields.Many2one('purchase.order', string='Заказ на закупку')
    state = fields.Selection(
        [('draft', 'Черновик'), ('recognized', 'Распознано'), ('done', 'Подтверждено')],
        default='draft',
    )
    recognized_amount = fields.Float(string='Распознанная сумма', readonly=True)
    recognized_date = fields.Char(string='Дата платежа (по документу)', readonly=True)
    recognized_purpose = fields.Char(string='Назначение платежа', readonly=True)
    recognized_recipient_inn = fields.Char(string='ИНН получателя (по документу)', readonly=True)
    recognized_recipient_name = fields.Char(string='Получатель (по документу)', readonly=True)
    recognized_payment_number = fields.Char(string='Номер платёжки', readonly=True)
    expected_amount = fields.Float(string='Сумма по заказу', readonly=True)
    partner_expected_inn = fields.Char(string='ИНН поставщика по заказу', readonly=True)
    inn_mismatch = fields.Boolean(string='ИНН не совпадает', readonly=True)

    def _get_order(self):
        return self.purchase_order_id or self.request_id.purchase_order_id

    def action_import(self):
        self.ensure_one()
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

        pdf_bytes = base64.b64decode(self.pdf_file)
        text = extract_text_from_pdf(pdf_bytes)
        use_vision = not text.strip() or pdf_is_scanned(pdf_bytes)

        try:
            if use_vision:
                images_png = render_pdf_pages_as_png(pdf_bytes)
                if not images_png:
                    raise UserError(_("Не удалось прочитать ни одной страницы из этого PDF."))
                data = call_llm_vision(
                    api_key, model, base_url, images_png,
                    PAYMENT_PROMPT_PLAIN_VISION, PAYMENT_PROMPT_STRUCTURED_VISION,
                    PAYMENT_SCHEMA, proxy_url)
            else:
                prompt_plain = PAYMENT_PROMPT_PLAIN.format(text=text[:20000])
                prompt_structured = PAYMENT_PROMPT_STRUCTURED.format(text=text[:20000])
                data = call_llm(
                    api_key, model, base_url, prompt_plain, prompt_structured,
                    PAYMENT_SCHEMA, proxy_url)
        except UserError:
            raise
        except Exception as exc:
            _logger.exception("LLM API call failed")
            raise UserError(_("Ошибка обращения к AI API: %s") % exc)

        if data.get('amount') is None:
            raise UserError(_("ИИ не смог распознать сумму платежа в этом документе."))

        order = self._get_order()
        recipient_inn = (data.get('recipient_inn') or '').strip()
        partner_inn = (order.partner_id.vat or '').strip() if order else ''
        inn_mismatch = bool(recipient_inn) and bool(partner_inn) and recipient_inn != partner_inn

        self.write({
            'recognized_amount': data.get('amount') or 0.0,
            'recognized_date': data.get('payment_date') or '',
            'recognized_purpose': data.get('purpose') or '',
            'recognized_recipient_inn': recipient_inn,
            'recognized_recipient_name': data.get('payee_name') or '',
            'recognized_payment_number': data.get('payment_number') or '',
            'expected_amount': order.amount_total if order else 0.0,
            'partner_expected_inn': partner_inn,
            'inn_mismatch': inn_mismatch,
            'state': 'recognized',
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.payment.import.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_confirm(self):
        """Human confirms the AI-recognized payment - nothing here is applied automatically.

        Per project policy (see AI PDF-import wizard), the AI only proposes
        recognized values; a person must explicitly click confirm before any
        order/request status changes, mirroring how invoice import always
        creates a draft order rather than auto-confirming it.
        """
        self.ensure_one()
        request = self.request_id
        order = self._get_order()

        # sudo() - прикрепление файла к заказу проверяет право на запись в
        # purchase.order, а у бухгалтера/кладовщика есть только чтение этой
        # модели (нужное им действие уже ограничено кнопкой/группой мастера).
        attachment = self.env['ir.attachment'].sudo().create({
            'name': self.pdf_filename or 'payment.pdf',
            'datas': self.pdf_file,
            'res_model': order._name if order else 'purchase.payment.import.wizard',
            'res_id': order.id if order else self.id,
            'mimetype': 'application/pdf',
        })

        recipient_inn = self.recognized_recipient_inn or ''
        partner_inn = self.partner_expected_inn or ''
        can_compare = bool(recipient_inn) and bool(partner_inn)
        partner_matched = can_compare and recipient_inn == partner_inn

        if order:
            self.env['purchase.payment.line'].create({
                'purchase_order_id': order.id,
                'payment_date': self.recognized_date,
                'payment_number': self.recognized_payment_number,
                'amount': self.recognized_amount,
                'recipient_name': self.recognized_recipient_name,
                'recipient_inn': recipient_inn,
                'purpose': self.recognized_purpose,
                'partner_matched': partner_matched,
                'attachment_id': attachment.id,
            })

        base_message = _("Оплата подтверждена (сумма %s, %s).") % (
            self.recognized_amount, self.recognized_purpose or 'без назначения')
        if can_compare and not partner_matched:
            message = base_message + '\n' + _(
                "ВНИМАНИЕ: ИНН получателя по документу (%s) не совпадает с "
                "ИНН поставщика по заказу (%s) - проверьте, куда ушёл платёж."
            ) % (recipient_inn, partner_inn)
        elif not can_compare:
            message = base_message + '\n' + _(
                "Не удалось сверить ИНН получателя с поставщиком (недостаточно данных)."
            )
        else:
            message = base_message

        if request:
            # При оплате после получения товар уже "На складе" к моменту
            # оплаты - напоминание организовать доставку тут неуместно,
            # заявка и так полностью завершена этим платежом.
            already_received = request.state == 'in_stock'
            if order:
                target_state = order._payment_target_state()
                order._advance_request_state(request, target_state)
            else:
                target_state = 'invoice_paid'
                request.write({'state': target_state})
            # Заявителю (обычному сотруднику цеха) не нужны сумма/назначение/
            # ИНН и сам PDF платёжки - это реквизиты, а не то, что его
            # касается. Ему важно только "оплачено или нет"; подробности и
            # вложение остаются в чате заказа (см. ниже), доступном
            # закупщику/бухгалтеру/главному закупщику.
            request_message = _("Оплата подтверждена.") if partner_matched or not can_compare \
                else _("Оплата подтверждена (требует проверки - см. заказ).")
            request.message_post(body=request_message)
            if target_state == 'invoice_paid' and not already_received:
                # Раньше активность назначалась на заявителя - но "Оформить
                # доставку" видна только закупщику/главному закупщику, у
                # заявителя нет ни доступа, ни кнопки, чтобы это сделать
                # (Мицуков сообщил, что видит "оплачено", но не может понять,
                # что дальше). Назначаем на ответственного закупщика заказа.
                responsible = order.user_id if order and order.user_id else self.env.user
                request.activity_schedule(
                    'mail.mail_activity_data_todo',
                    summary=_("Счёт оплачен - нужно организовать получение/вызвать доставку"),
                    user_id=responsible.id,
                )

        # Платёжка должна быть видна и в чате заказа тоже, а не только в
        # чате заявки (раньше постилось только куда-то одно из двух).
        if order:
            order.sudo().message_post(body=message, attachment_ids=[attachment.id])
            order.sudo()._close_payment_activities()

        if order and can_compare and not partner_matched:
            order.sudo().activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_("Проверить платёж - ИНН получателя не совпадает с ИНН поставщика"),
                note=_("ИНН по документу: %s. ИНН поставщика по заказу: %s.") % (
                    recipient_inn, partner_inn),
                user_id=(order.user_id.id or self.env.user.id),
            )

        self.write({'state': 'done'})
        return {'type': 'ir.actions.act_window_close'}
