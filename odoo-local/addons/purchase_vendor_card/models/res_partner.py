import operator as py_operator
import re
from collections import Counter, defaultdict
from datetime import timedelta

from odoo import _, api, fields, models

_COMPARE_OPS = {
    '=': py_operator.eq, '!=': py_operator.ne,
    '<': py_operator.lt, '<=': py_operator.le,
    '>': py_operator.gt, '>=': py_operator.ge,
}

from odoo.addons.purchase_vendor_matching.wizard.inn_utils import normalize_inn

# Юр. форма + кавычки/пробелы/регистр - тот же список сокращений, что и в
# build_short_name (purchase_registry_ux/models/res_partner.py), но здесь
# другая задача (не короткая подпись для колонки, а ключ для группировки
# дублей), поэтому не переиспользуется как есть, а не дублируется как факт
# кода - см. NOTES.md.
_LEGAL_FORM_RE = re.compile(
    r'^(ооо|зао|оао|пао|ао|ип|чп|тов)\b\.?\s*', re.IGNORECASE)


def _normalize_vendor_name(name):
    """Ключ для сравнения названий поставщиков без учёта юр. формы,
    кавычек, пробелов и регистра - 'ООО "Дата-В"' и 'ООО Дата-В' должны
    дать одинаковый ключ (п. 8.6 ТЗ)."""
    if not name:
        return ''
    value = name.strip().lower()
    value = _LEGAL_FORM_RE.sub('', value)
    value = value.strip(' \t\n"\'«»')
    value = re.sub(r'[\s"\'«»]+', ' ', value)
    return value.strip()


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # --- 8.4: списки для вкладок - обычные One2many, без compute (Odoo сам
    # разрешает их через уже существующее обратное поле partner_id, никакого
    # native-поля с таким именем на res.partner в базовом Odoo нет). ---------
    vendor_order_ids = fields.One2many(
        'purchase.order', 'partner_id', string='Заказы поставщика')
    vendor_supplierinfo_ids = fields.One2many(
        'product.supplierinfo', 'partner_id', string='Прайс-лист поставщика')

    # --- 8.2/8.3: финансовые показатели, СЧИТАЮТСЯ НА ЛЕТУ ------------------
    # store=True здесь не даёт corrent - purchase.order.amount_paid сам не
    # stored (см. NOTES.md), поэтому строить над ним stored-цепочку нельзя:
    # значения будут расходиться с реальностью. Один @api.depends() (пустой)
    # на группу полей - пересчёт происходит при каждом свежем обращении в
    # новой транзакции/запросе, а не на каждую запись каждого поля отдельно.
    vendor_currency_id = fields.Many2one(
        'res.currency', compute='_compute_vendor_currency_id',
        string='Валюта (для сумм по закупкам)')
    vendor_invoiced_total = fields.Monetary(
        compute='_compute_vendor_stats', currency_field='vendor_currency_id',
        string='Выставлено счетов')
    vendor_paid_total = fields.Monetary(
        compute='_compute_vendor_stats', currency_field='vendor_currency_id',
        string='Оплачено')
    vendor_frozen_total = fields.Monetary(
        compute='_compute_vendor_stats', currency_field='vendor_currency_id',
        search='_search_vendor_frozen_total',
        string='Оплачено и едет',
        help='Оплачено, но товар по этим заказам ещё не получен.')
    vendor_debt_total = fields.Monetary(
        compute='_compute_vendor_stats', currency_field='vendor_currency_id',
        search='_search_vendor_debt_total',
        string='Мы должны',
        help='Товар получен, остаток по этим заказам ещё не погашен.')
    vendor_residual_total = fields.Monetary(
        compute='_compute_vendor_stats', currency_field='vendor_currency_id',
        string='Остаток к оплате')
    vendor_order_count = fields.Integer(
        compute='_compute_vendor_stats', string='Заказов')
    vendor_avg_order = fields.Monetary(
        compute='_compute_vendor_stats', currency_field='vendor_currency_id',
        string='Средний чек')
    vendor_last_order_date = fields.Date(
        compute='_compute_vendor_stats', search='_search_vendor_last_order_date',
        string='Последний заказ')
    vendor_missing_docs_count = fields.Integer(
        compute='_compute_vendor_stats', string='Заказов без документов')

    vendor_turnover_period = fields.Monetary(
        compute='_compute_vendor_turnover_period', currency_field='vendor_currency_id',
        string='Оборот за период',
        help='Период задаётся фильтром поиска (месяц/квартал/год/с начала '
             'работы) - по умолчанию текущий месяц.')

    def _compute_vendor_currency_id(self):
        currency = self.env.company.currency_id
        for partner in self:
            partner.vendor_currency_id = currency

    def _get_vendor_order_rows(self):
        """Данные по заказам ВСЕХ партнёров из self одним запросом (плюс один
        запрос по приёмкам) - см. п. 8.3 ТЗ: "никаких вычислений в цикле по
        записям". sudo() - показываем агрегаты всем ролям из п. 8.7, реальную
        границу доступа держат groups= на вкладках/колонках вида, не ACL
        отдельных заказов (тот же приём, что и в purchase_finance_dashboard)."""
        partner_ids = self.ids
        if not partner_ids:
            return [], set()
        Order = self.env['purchase.order'].sudo()
        rows = Order.search_read(
            [('partner_id', 'in', partner_ids)],
            ['id', 'partner_id', 'amount_total', 'amount_paid', 'date_order', 'document_status'])
        order_ids = [row['id'] for row in rows]
        received_ids = set()
        if order_ids:
            # "Товар получен" - через проведённую входящую приёмку
            # (stock_picking), НЕ через request_state - тот же класс бага,
            # что уже описан и покрыт тестом в purchase_finance_dashboard
            # (п. 7.1/8.2 ТЗ прямо требуют не повторять его).
            pickings = self.env['stock.picking'].sudo().search([
                ('source_purchase_order_id', 'in', order_ids),
                ('state', '=', 'done'),
                ('picking_type_id.code', '=', 'incoming'),
            ])
            received_ids = set(pickings.mapped('source_purchase_order_id').ids)
        return rows, received_ids

    @api.depends()
    def _compute_vendor_stats(self):
        rows, received_ids = self._get_vendor_order_rows()
        stats = defaultdict(lambda: {
            'invoiced': 0.0, 'paid': 0.0, 'frozen': 0.0, 'debt': 0.0,
            'count': 0, 'missing_docs': 0, 'last_date': False,
        })
        for row in rows:
            partner_id = row['partner_id'][0]
            bucket = stats[partner_id]
            total = row['amount_total'] or 0.0
            paid = row['amount_paid'] or 0.0
            bucket['invoiced'] += total
            bucket['paid'] += paid
            bucket['count'] += 1
            # Заказ не может быть одновременно "едет" и "должны" - тест
            # test_frozen_and_debt_are_mutually_exclusive это проверяет.
            if row['id'] in received_ids:
                bucket['debt'] += total - paid
            else:
                bucket['frozen'] += paid
            if row['document_status'] == 'blocked':
                bucket['missing_docs'] += 1
            order_date = row['date_order']
            if order_date and (not bucket['last_date'] or order_date > bucket['last_date']):
                bucket['last_date'] = order_date
        for partner in self:
            bucket = stats.get(partner.id, {})
            invoiced = bucket.get('invoiced', 0.0)
            count = bucket.get('count', 0)
            partner.vendor_invoiced_total = invoiced
            partner.vendor_paid_total = bucket.get('paid', 0.0)
            partner.vendor_frozen_total = bucket.get('frozen', 0.0)
            partner.vendor_debt_total = bucket.get('debt', 0.0)
            partner.vendor_residual_total = invoiced - bucket.get('paid', 0.0)
            partner.vendor_order_count = count
            partner.vendor_avg_order = (invoiced / count) if count else 0.0
            last_date = bucket.get('last_date')
            partner.vendor_last_order_date = last_date.date() if last_date else False
            partner.vendor_missing_docs_count = bucket.get('missing_docs', 0)

    def _get_all_suppliers_stats(self):
        """Тот же расчёт, что и _compute_vendor_stats, но по ВСЕМ
        поставщикам компании и в виде словаря {partner_id: bucket} -
        переиспользуется search()-методами ниже, чтобы фильтры по не-stored
        полям (Есть долг/Есть заморозка/Не заказывали > N дней) реально
        работали, а не только показывали значение на уже открытой карточке.
        Одна и та же пара запросов на любое число поставщиков - тот же
        принцип "не в цикле по записям", что и в самом compute (п. 8.3 ТЗ)."""
        Partner = self.env['res.partner'].sudo()
        suppliers = Partner.search([('supplier_rank', '>', 0)])
        rows, received_ids = suppliers._get_vendor_order_rows()
        stats = defaultdict(lambda: {
            'invoiced': 0.0, 'paid': 0.0, 'frozen': 0.0, 'debt': 0.0,
            'count': 0, 'missing_docs': 0, 'last_date': False,
        })
        for row in rows:
            partner_id = row['partner_id'][0]
            bucket = stats[partner_id]
            total = row['amount_total'] or 0.0
            paid = row['amount_paid'] or 0.0
            bucket['invoiced'] += total
            bucket['paid'] += paid
            bucket['count'] += 1
            if row['id'] in received_ids:
                bucket['debt'] += total - paid
            else:
                bucket['frozen'] += paid
            if row['document_status'] == 'blocked':
                bucket['missing_docs'] += 1
            order_date = row['date_order']
            if order_date and (not bucket['last_date'] or order_date > bucket['last_date']):
                bucket['last_date'] = order_date
        return stats

    def _search_vendor_money_field(self, bucket_key, operator_str, value):
        op = _COMPARE_OPS.get(operator_str)
        if op is None:
            raise NotImplementedError(_('Оператор %s не поддерживается для этого поля.') % operator_str)
        stats = self._get_all_suppliers_stats()
        matching_ids = [pid for pid, bucket in stats.items() if op(bucket[bucket_key], value)]
        return [('id', 'in', matching_ids)]

    def _search_vendor_frozen_total(self, operator, value):
        return self._search_vendor_money_field('frozen', operator, value)

    def _search_vendor_debt_total(self, operator, value):
        return self._search_vendor_money_field('debt', operator, value)

    def _search_vendor_last_order_date(self, operator, value):
        op = _COMPARE_OPS.get(operator)
        if op is None:
            raise NotImplementedError(_('Оператор %s не поддерживается для этого поля.') % operator)
        value_date = fields.Date.from_string(value) if isinstance(value, str) else value
        never_ordered = fields.Date.from_string('1900-01-01')
        stats = self._get_all_suppliers_stats()
        matching_ids = []
        for pid, bucket in stats.items():
            last_dt = bucket['last_date']
            last_date = last_dt.date() if last_dt else never_ordered
            if op(last_date, value_date):
                matching_ids.append(pid)
        # Поставщики без единого заказа вообще не попадают в stats (там нет
        # заказов, значит нет и строк в rows) - для "не заказывали > N дней"
        # их тоже нужно включить (см. NOTES.md).
        if op(never_ordered, value_date):
            all_supplier_ids = self.env['res.partner'].sudo().search([('supplier_rank', '>', 0)]).ids
            matching_ids = list(set(matching_ids) | (set(all_supplier_ids) - set(stats.keys())))
        return [('id', 'in', matching_ids)]

    def _vendor_period_bounds(self):
        """Границы периода из контекста (vendor_stats_from/_to, строки
        'YYYY-MM-DD'), по умолчанию - текущий месяц (п. 8.3 ТЗ). Верхняя
        граница - исключающая (следующий день после vendor_stats_to), чтобы
        не терять заказы, оформленные в последний день периода позже полуночи
        (date_order - datetime, не date)."""
        ctx = self.env.context
        today = fields.Date.context_today(self)
        date_from = ctx.get('vendor_stats_from')
        date_to = ctx.get('vendor_stats_to')
        date_from = fields.Date.from_string(date_from) if date_from else today.replace(day=1)
        date_to = fields.Date.from_string(date_to) if date_to else today
        return date_from, date_to + timedelta(days=1)

    @api.depends_context('vendor_stats_from', 'vendor_stats_to')
    def _compute_vendor_turnover_period(self):
        partner_ids = self.ids
        totals = defaultdict(float)
        if partner_ids:
            date_from, date_to_excl = self._vendor_period_bounds()
            rows = self.env['purchase.order'].sudo().search_read(
                [('partner_id', 'in', partner_ids),
                 ('date_order', '>=', fields.Date.to_string(date_from)),
                 ('date_order', '<', fields.Date.to_string(date_to_excl))],
                ['partner_id', 'amount_total'])
            for row in rows:
                totals[row['partner_id'][0]] += row['amount_total'] or 0.0
        for partner in self:
            partner.vendor_turnover_period = totals.get(partner.id, 0.0)

    # --- 8.6: качество данных и дубли ---------------------------------------
    # В отличие от финансовых полей выше, это store=True (п. 8.6 явно
    # называет их "индикатор"/"колонка-бейдж в списке... и фильтр" - нужна
    # SQL-фильтрация по ним, а non-stored compute-поле в domain не работает).
    vendor_data_quality = fields.Selection([
        ('ok', 'В порядке'),
        ('incomplete', 'Не хватает данных'),
        ('critical', 'Нет ИНН'),
    ], compute='_compute_vendor_data_quality', store=True, string='Полнота карточки')

    @api.depends('vat', 'kpp', 'phone', 'bank_ids', 'supplier_rank')
    def _compute_vendor_data_quality(self):
        for partner in self:
            if not partner.supplier_rank:
                partner.vendor_data_quality = False
            elif not partner.vat:
                partner.vendor_data_quality = 'critical'
            elif not partner.kpp or not partner.phone or not partner.bank_ids:
                partner.vendor_data_quality = 'incomplete'
            else:
                partner.vendor_data_quality = 'ok'

    vendor_duplicate_group_key = fields.Char(
        compute='_compute_vendor_duplicate_group_key', store=True,
        help='Нормализованный ИНН, если он есть, иначе нормализованное '
             'название без юр. формы/кавычек/регистра.')
    vendor_is_possible_duplicate = fields.Boolean(
        compute='_compute_vendor_is_possible_duplicate', store=True,
        string='Возможный дубль')

    @api.depends('vat', 'name', 'supplier_rank')
    def _compute_vendor_duplicate_group_key(self):
        for partner in self:
            if not partner.supplier_rank:
                partner.vendor_duplicate_group_key = False
                continue
            inn = normalize_inn(partner.vat)
            if inn:
                partner.vendor_duplicate_group_key = 'inn:%s' % inn
            else:
                partner.vendor_duplicate_group_key = 'name:%s' % _normalize_vendor_name(partner.name)

    @api.depends('vendor_duplicate_group_key')
    def _compute_vendor_is_possible_duplicate(self):
        # Считаем по ВСЕМ поставщикам, не только по self - иначе при
        # пересчёте одной записи (например, после правки её ИНН) соседняя
        # запись группы, чей ИНН не менялся, не обновится сама (её
        # собственные depends-поля не изменились) - см. NOTES.md про этот
        # компромисс и почему action_open_vendor_duplicates форсирует полный
        # пересчёт перед показом списка, а не полагается только на кэш.
        all_suppliers = self.search([
            ('supplier_rank', '>', 0), ('vendor_duplicate_group_key', '!=', False)])
        counts = Counter(all_suppliers.mapped('vendor_duplicate_group_key'))
        for partner in self:
            key = partner.vendor_duplicate_group_key
            partner.vendor_is_possible_duplicate = bool(key) and counts.get(key, 0) > 1

    def action_open_vendor_duplicates(self):
        """Готовит выборку кандидатов в дубли и открывает её списком -
        объединение делается уже штатным мастером Odoo (см. README.md:
        base.action_partner_merge, привязан к списку res.partner и появится
        в меню "Действие" сам, свой мастер объединения не пишем, п. 8.6 ТЗ)."""
        suppliers = self.search([('supplier_rank', '>', 0)])
        suppliers._compute_vendor_duplicate_group_key()
        suppliers._compute_vendor_is_possible_duplicate()
        duplicates = suppliers.filtered('vendor_is_possible_duplicate')
        return {
            'type': 'ir.actions.act_window',
            'name': _('Возможные дубли поставщиков'),
            'res_model': 'res.partner',
            'view_mode': 'list,form',
            'views': [(self.env.ref('purchase_vendor_card.view_vendor_duplicates_list').id, 'list'),
                      (False, 'form')],
            'domain': [('id', 'in', duplicates.ids)],
            'context': {'search_default_group_by_key': 1},
        }

    # --- 8.4: smart-кнопки ---------------------------------------------------

    def action_view_vendor_requests(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Заявки поставщика'),
            'res_model': 'purchase.request',
            'view_mode': 'list,form',
            'domain': [('purchase_order_id.partner_id', '=', self.id)],
        }

    def action_view_vendor_payments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Платежи поставщику'),
            'res_model': 'purchase.payment.line',
            'view_mode': 'list,form',
            'domain': [('purchase_order_id.partner_id', '=', self.id)],
        }

    def action_view_vendor_debt_orders(self):
        """"Мы должны" - список заказов, где товар получен, а остаток не
        погашен (та же логика, что в _compute_vendor_stats, но здесь нужны
        именно id заказов, а не сумма)."""
        self.ensure_one()
        rows, received_ids = self._get_vendor_order_rows()
        debt_order_ids = [
            row['id'] for row in rows
            if row['id'] in received_ids and (row['amount_total'] or 0.0) - (row['amount_paid'] or 0.0) > 0.01
        ]
        return {
            'type': 'ir.actions.act_window',
            'name': _('Получено, не оплачено'),
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', debt_order_ids)],
        }

    def action_view_vendor_price_history(self):
        """"История цен" (п. 8.4 ТЗ) - pivot по purchase.order.line этого
        поставщика, товар × месяц. Мера по умолчанию - сумма price_unit;
        переключить на "Среднее" - стандартным переключателем меры в самом
        pivot-виде Odoo (значок Ø на измерении), отдельная кнопка/код под
        это не заводились - см. README.md."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('История цен - %s') % self.name,
            'res_model': 'purchase.order.line',
            'view_mode': 'pivot,list',
            'views': [(self.env.ref('purchase_vendor_card.view_vendor_price_history_pivot').id, 'pivot'),
                      (False, 'list')],
            'domain': [('order_id.partner_id', '=', self.id)],
        }
