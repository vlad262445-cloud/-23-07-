import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

from .proxy_utils import build_proxy_url

_logger = logging.getLogger(__name__)

DEFAULT_MODEL = 'claude-haiku-4-5'
TEST_URL_ANTHROPIC = 'https://api.anthropic.com/v1/models'


class PurchasePdfImportSettings(models.TransientModel):
    _name = 'purchase.pdf.import.settings'
    _description = 'Настройки ИИ для импорта PDF'

    anthropic_api_key = fields.Char(string='API-ключ')
    base_url = fields.Char(
        string='Кастомный Base URL',
        help="Оставьте пустым, чтобы использовать Claude API от Anthropic "
             "напрямую. Укажите здесь адрес, чтобы вместо этого обращаться "
             "к OpenAI-совместимому эндпоинту (например, свой шлюз/прокси) "
             "- тогда ключ выше используется как ключ этого эндпоинта.",
    )
    model = fields.Char(string='Модель', default=DEFAULT_MODEL)

    proxy_host = fields.Char(string='Прокси: IP/хост')
    proxy_port = fields.Char(string='Прокси: порт')
    proxy_login = fields.Char(string='Прокси: логин')
    proxy_password = fields.Char(string='Прокси: пароль')
    proxy_required = fields.Boolean(
        string='Останавливать импорт, если прокси недоступен',
        default=True,
        help="Пока указан хост прокси, запросы к ИИ в любом случае идут "
             "ТОЛЬКО через прокси - резервного прямого запроса с IP сервера "
             "нет в принципе. Эта галочка включает быструю проверку прокси "
             "перед каждым импортом: если она включена и прокси не отвечает, "
             "импорт сразу останавливается с понятной ошибкой, не дожидаясь "
             "долгого тайм-аута самого запроса к ИИ. Если выключить - прокси "
             "используется точно так же, просто ошибка (если он упал) будет "
             "получена от самого запроса к ИИ, а не сразу.",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        icp = self.env['ir.config_parameter'].sudo()
        res['anthropic_api_key'] = icp.get_param('purchase_pdf_import.anthropic_api_key', '')
        res['base_url'] = icp.get_param('purchase_pdf_import.base_url', '')
        res['model'] = icp.get_param('purchase_pdf_import.model', DEFAULT_MODEL)
        res['proxy_host'] = icp.get_param('purchase_pdf_import.proxy_host', '')
        res['proxy_port'] = icp.get_param('purchase_pdf_import.proxy_port', '')
        res['proxy_login'] = icp.get_param('purchase_pdf_import.proxy_login', '')
        res['proxy_password'] = icp.get_param('purchase_pdf_import.proxy_password', '')
        res['proxy_required'] = icp.get_param('purchase_pdf_import.proxy_required', '1') == '1'
        return res

    def action_save(self):
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('purchase_pdf_import.anthropic_api_key', self.anthropic_api_key or '')
        icp.set_param('purchase_pdf_import.base_url', self.base_url or '')
        icp.set_param('purchase_pdf_import.model', self.model or DEFAULT_MODEL)
        icp.set_param('purchase_pdf_import.proxy_host', self.proxy_host or '')
        icp.set_param('purchase_pdf_import.proxy_port', self.proxy_port or '')
        icp.set_param('purchase_pdf_import.proxy_login', self.proxy_login or '')
        icp.set_param('purchase_pdf_import.proxy_password', self.proxy_password or '')
        icp.set_param('purchase_pdf_import.proxy_required', '1' if self.proxy_required else '0')
        return {'type': 'ir.actions.act_window_close'}

    def action_test_connection(self):
        self.ensure_one()
        proxy_url = build_proxy_url(
            self.proxy_host, self.proxy_port, self.proxy_login, self.proxy_password)
        if not proxy_url:
            raise UserError("Сначала укажите хотя бы IP/хост прокси.")

        target_url = self.base_url.strip() if self.base_url else TEST_URL_ANTHROPIC

        import httpx
        try:
            with httpx.Client(proxy=proxy_url, timeout=15) as client:
                response = client.get(target_url)
        except Exception as exc:
            _logger.warning("Proxy test failed: %s", exc)
            raise UserError(f"Не удалось подключиться через прокси: {exc}")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Прокси работает',
                'message': f"Соединение через {self.proxy_host}:{self.proxy_port or '8080'} "
                           f"установлено, эндпоинт ответил HTTP {response.status_code}.",
                'type': 'success',
                'sticky': False,
            },
        }
