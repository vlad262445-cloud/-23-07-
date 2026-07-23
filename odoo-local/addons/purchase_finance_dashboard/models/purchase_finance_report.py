from odoo import fields, models, tools


class PurchaseFinanceReport(models.Model):
    """Ядро всех 4 дашбордов (п. 7.1 ТЗ) - read-only SQL-view, одна строка на
    заказ. Бесплатно получает pivot/graph/list/search/group_by/XLSX/"Вставить
    в таблицу" - см. README.md."""
    _name = 'purchase.finance.report'
    _description = 'Финансовый отчёт по закупкам'
    _auto = False
    _order = 'date_order desc'
    _rec_name = 'order_id'

    order_id = fields.Many2one('purchase.order', string='Закупка', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Поставщик', readonly=True)
    requester_id = fields.Many2one('res.users', string='Заказчик', readonly=True)
    user_id = fields.Many2one('res.users', string='Закупщик', readonly=True)
    department_id = fields.Many2one(
        'purchase.request.department', string='Отдел/участок', readonly=True)

    cost_analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Статья затрат', readonly=True)
    cost_category_id = fields.Many2one(
        'account.analytic.account', string='Категория', readonly=True)
    cost_plan_id = fields.Many2one(
        'account.analytic.plan', string='План статьи', readonly=True)
    cost_plan_root_id = fields.Many2one(
        'account.analytic.plan', string='Верхний уровень статьи', readonly=True)
    cost_code = fields.Char(string='Код статьи', readonly=True)
    cost_category_code = fields.Char(string='Код категории', readonly=True)

    date_order = fields.Datetime(string='Дата заказа', readonly=True)
    date_month = fields.Char(string='Месяц', readonly=True)
    date_quarter = fields.Char(string='Квартал', readonly=True)
    date_year = fields.Char(string='Год', readonly=True)

    payment_type = fields.Selection([
        ('full_prepay', 'Полная предоплата'),
        ('split_50_50', '50% предоплата + 50% после получения'),
        ('post_payment', 'Оплата после получения'),
    ], string='Тип оплаты', readonly=True)
    payment_priority = fields.Selection([
        ('0', 'Обычная'), ('1', 'Срочно'), ('2', 'Критично'),
    ], string='Приоритет заказа', readonly=True)
    # Значения - LIFECYCLE_STAGES из purchase_registry_ux (мягкая
    # зависимость, см. init() - модуль может быть не установлен, п. 11.1 ТЗ).
    lifecycle_stage = fields.Selection([
        ('draft', 'Черновик'), ('to_approve', 'На согласовании'),
        ('approved', 'Согласовано'), ('prepaid', 'Предоплачено'),
        ('in_transit', 'В пути'), ('in_stock', 'На складе'),
        ('completed', 'Завершена'), ('declined', 'Отклонена'),
        ('cancel', 'Отменена'),
    ], string='Этап', readonly=True)

    amount_total = fields.Monetary(string='Сумма заказа', currency_field='currency_id', readonly=True)
    amount_paid = fields.Monetary(string='Оплачено', currency_field='currency_id', readonly=True)
    amount_residual = fields.Monetary(string='Остаток к оплате', currency_field='currency_id', readonly=True)
    amount_frozen = fields.Monetary(
        string='Заморожено в предоплате', currency_field='currency_id', readonly=True,
        help='Оплачено, но товар ещё не получен.')
    amount_debt_received = fields.Monetary(
        string='Получено, не оплачено', currency_field='currency_id', readonly=True,
        help='Кредиторская задолженность - товар получен, остаток не погашен.')
    amount_unpaid_urgent = fields.Monetary(
        string='Не оплачено срочного', currency_field='currency_id', readonly=True)
    currency_id = fields.Many2one('res.currency', string='Валюта', readonly=True)

    is_received = fields.Boolean(string='Товар получен', readonly=True)
    days_frozen = fields.Integer(string='Дней в заморозке', readonly=True)
    days_debt = fields.Integer(string='Дней в долге', readonly=True)
    days_in_stage = fields.Integer(string='Дней на этапе', readonly=True)
    has_missing_docs = fields.Boolean(string='Не хватает документов', readonly=True)
    # П. 7.5 ТЗ, экран 2 ("Заморозка по возрасту") - интервалы заданы в самом
    # ТЗ буквально (0-7/8-14/15-30/31-60/60+), считаются один раз в SQL, а не
    # через group_by по days_frozen (иначе получилось бы по строке на
    # КАЖДОЕ значение дней, а не 5 осмысленных корзин).
    days_frozen_bucket = fields.Selection([
        ('0_7', '0-7 дней'), ('8_14', '8-14 дней'), ('15_30', '15-30 дней'),
        ('31_60', '31-60 дней'), ('60_plus', '60+ дней'),
    ], string='Заморозка (возраст)', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        order_fields = self.env['purchase.order']._fields
        # Мягкие зависимости от модулей 1/5 (purchase_registry_ux) - п. 11.1
        # ТЗ: "модули 6 и 7 используют поля модулей 3-5, но должны
        # устанавливаться и работать без них". lifecycle_stage/
        # lifecycle_stage_since - реальные колонки purchase_order ТОЛЬКО если
        # соответствующий модуль установлен; иначе - NULL. days_in_stage
        # (lifecycle_stage_since) объявлен в ЭТОМ ЖЕ модуле (models/
        # purchase_order.py), поэтому колонка гарантированно существует к
        # моменту init() (Odoo сначала прогоняет _auto_init всех моделей
        # текущей загрузки, потом init() всех SQL-view) - его достаточно
        # защитить только тем, что сам lifecycle_stage может отсутствовать.
        has_lifecycle_stage = 'lifecycle_stage' in order_fields
        lifecycle_stage_select = 'po.lifecycle_stage' if has_lifecycle_stage else 'NULL::varchar'

        self.env.cr.execute(f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT
                    po.id AS id,
                    po.id AS order_id,
                    po.partner_id AS partner_id,
                    po.requester_id AS requester_id,
                    po.user_id AS user_id,
                    dept.department_id AS department_id,

                    po.cost_analytic_account_id AS cost_analytic_account_id,
                    po.cost_category_id AS cost_category_id,
                    caa.plan_id AS cost_plan_id,
                    caa.root_plan_id AS cost_plan_root_id,
                    caa.code AS cost_code,
                    cat.code AS cost_category_code,

                    po.date_order AS date_order,
                    to_char(po.date_order, 'YYYY-MM') AS date_month,
                    to_char(po.date_order, 'YYYY') || '-Q'
                        || date_part('quarter', po.date_order)::int::text AS date_quarter,
                    to_char(po.date_order, 'YYYY') AS date_year,

                    po.payment_type AS payment_type,
                    po.payment_priority AS payment_priority,
                    {lifecycle_stage_select} AS lifecycle_stage,

                    po.amount_total AS amount_total,
                    COALESCE(pay.amount_paid, 0) AS amount_paid,
                    (po.amount_total - COALESCE(pay.amount_paid, 0)) AS amount_residual,
                    CASE WHEN NOT COALESCE(recv.is_received, false)
                         THEN COALESCE(pay.amount_paid, 0) ELSE 0 END AS amount_frozen,
                    CASE WHEN COALESCE(recv.is_received, false)
                         THEN (po.amount_total - COALESCE(pay.amount_paid, 0)) ELSE 0 END AS amount_debt_received,
                    CASE WHEN po.payment_priority IN ('1', '2')
                         THEN (po.amount_total - COALESCE(pay.amount_paid, 0)) ELSE 0 END AS amount_unpaid_urgent,
                    po.currency_id AS currency_id,

                    COALESCE(recv.is_received, false) AS is_received,
                    CASE WHEN NOT COALESCE(recv.is_received, false) AND pay.first_payment_date IS NOT NULL
                         THEN GREATEST(0, EXTRACT(DAY FROM (now() - pay.first_payment_date))::int)
                         ELSE 0 END AS days_frozen,
                    CASE WHEN COALESCE(recv.is_received, false) OR pay.first_payment_date IS NULL THEN NULL
                         ELSE (CASE
                             WHEN now() - pay.first_payment_date <= interval '7 day' THEN '0_7'
                             WHEN now() - pay.first_payment_date <= interval '14 day' THEN '8_14'
                             WHEN now() - pay.first_payment_date <= interval '30 day' THEN '15_30'
                             WHEN now() - pay.first_payment_date <= interval '60 day' THEN '31_60'
                             ELSE '60_plus' END)
                    END AS days_frozen_bucket,
                    CASE WHEN COALESCE(recv.is_received, false)
                              AND (po.amount_total - COALESCE(pay.amount_paid, 0)) > 0.01
                              AND recv.received_date IS NOT NULL
                         THEN GREATEST(0, EXTRACT(DAY FROM (now() - recv.received_date))::int)
                         ELSE 0 END AS days_debt,
                    CASE WHEN po.lifecycle_stage_since IS NOT NULL
                         THEN GREATEST(0, EXTRACT(DAY FROM (now() - po.lifecycle_stage_since))::int)
                         ELSE 0 END AS days_in_stage,
                    (po.document_status = 'blocked') AS has_missing_docs

                FROM purchase_order po
                LEFT JOIN account_analytic_account caa ON caa.id = po.cost_analytic_account_id
                LEFT JOIN account_analytic_account cat ON cat.id = po.cost_category_id
                LEFT JOIN LATERAL (
                    SELECT pr.department_id AS department_id
                    FROM purchase_request pr
                    WHERE pr.purchase_order_id = po.id
                    ORDER BY pr.id
                    LIMIT 1
                ) dept ON true
                LEFT JOIN LATERAL (
                    SELECT sum(ppl.amount) AS amount_paid, min(ppl.create_date) AS first_payment_date
                    FROM purchase_payment_line ppl
                    WHERE ppl.purchase_order_id = po.id
                ) pay ON true
                LEFT JOIN LATERAL (
                    -- "Товар получен" - через проведённую входящую приёмку
                    -- (stock_picking), НЕ через request_state - на маршрутах
                    -- post_payment/split_50_50 request_state после оплаты
                    -- уходит в invoice_paid, и "== in_stock" дал бы ложный
                    -- ноль (тот же класс бага, что уже описан в комментариях
                    -- purchase_pdf_import, п. 7.1 ТЗ прямо требует не
                    -- повторять его - см. regression-тест в tests/).
                    SELECT bool_or(true) AS is_received, max(sp.date_done) AS received_date
                    FROM stock_picking sp
                    JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
                    WHERE sp.source_purchase_order_id = po.id
                          AND sp.state = 'done' AND spt.code = 'incoming'
                ) recv ON true
            )
        """)
