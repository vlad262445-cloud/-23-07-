from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRolePermissions(TransactionCase):
    """Кто что может создавать/писать - зафиксировано здесь, чтобы будущая
    правка ir.model.access.csv/групп не тихо открыла или не закрыла доступ,
    который для роли был решён сознательно (см. память odoo_purchase_roles_model).

    Каждая строка ниже была вручную перепроверена через shell на проде
    2026-07-10 - этот файл превращает ту разовую проверку в постоянный тест.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ref = cls.env.ref
        group_user = ref('base.group_user')
        group_buyer = ref('purchase.group_purchase_user')
        group_chief_buyer = ref('purchase_pdf_import.group_chief_buyer')
        group_accountant = ref('purchase_pdf_import.group_accountant')
        group_keeper = ref('purchase_pdf_import.group_warehouse_keeper')

        def make_user(login, groups):
            return cls.env['res.users'].create({
                'name': login,
                'login': login,
                'email': f'{login}@example.com',
                'groups_id': [(6, 0, [g.id for g in groups])],
            })

        cls.users = {
            'dept': make_user('test_role_dept', [group_user]),
            'buyer': make_user('test_role_buyer', [group_buyer, group_keeper]),
            'chief_buyer': make_user('test_role_chief_buyer', [group_chief_buyer]),
            'accountant': make_user('test_role_accountant', [group_accountant]),
            'keeper': make_user('test_role_keeper', [group_keeper]),
        }

    def _assert_create_access(self, model, role, expected):
        env_as_role = self.env[model].with_user(self.users[role])
        if expected:
            try:
                env_as_role.check_access('create')
            except AccessError:
                self.fail(
                    f'{role} должен(на) иметь право create на {model}, но получил(а) отказ')
        else:
            with self.assertRaises(
                    AccessError,
                    msg=f'{role} НЕ должен(на) иметь право create на {model}, но получил(а) доступ'):
                env_as_role.check_access('create')

    def _assert_write_access(self, model, role, expected):
        env_as_role = self.env[model].with_user(self.users[role])
        if expected:
            try:
                env_as_role.check_access('write')
            except AccessError:
                self.fail(
                    f'{role} должен(на) иметь право write на {model}, но получил(а) отказ')
        else:
            with self.assertRaises(
                    AccessError,
                    msg=f'{role} НЕ должен(на) иметь право write на {model}, но получил(а) доступ'):
                env_as_role.check_access('write')

    # Импорт УПД - Кладовщик и Главный закупщик могут, остальные нет
    # (см. коммиты 2026-07-10 "Let Главный закупщик import УПД themselves").
    CREATE_MATRIX = {
        # Оформление заказа из заявки (ИИ-импорт счёта) - Закупщик и Главный
        # закупщик могут, остальные нет. Асадуллин (Главный закупщик) не
        # видел кнопку "Оформить заказ" вообще - выяснилось, что группа
        # была указана только для Закупщика, а модель вообще не была
        # доступна Главному закупщику (см. коммит про эту кнопку).
        'purchase.pdf.import.wizard': {
            'dept': False, 'buyer': True, 'chief_buyer': True,
            'accountant': False, 'keeper': False,
        },
        # Разделение заявки на несколько (когда позиции нужно закупать у
        # разных поставщиков) - те же роли, что и оформляют заказ.
        'purchase.request.split.wizard': {
            'dept': False, 'buyer': True, 'chief_buyer': True,
            'accountant': False, 'keeper': False,
        },
        'purchase.request.split.wizard.line': {
            'dept': False, 'buyer': True, 'chief_buyer': True,
            'accountant': False, 'keeper': False,
        },
        # Объединение заявок (обратная операция к split) - те же роли.
        'purchase.request.merge.wizard': {
            'dept': False, 'buyer': True, 'chief_buyer': True,
            'accountant': False, 'keeper': False,
        },
        'purchase.updd.import.wizard': {
            'dept': False, 'buyer': True, 'chief_buyer': True,
            'accountant': False, 'keeper': True,
        },
        'purchase.updd.import.wizard.line': {
            'dept': False, 'buyer': True, 'chief_buyer': True,
            'accountant': False, 'keeper': True,
        },
        'purchase.updd.line': {
            'dept': False, 'buyer': True, 'chief_buyer': True,
            'accountant': False, 'keeper': True,
        },
        # Платёжки - только Бухгалтер (Закупщик потерял этот доступ 2026-07-10,
        # см. коммит "Lock down payment import to Бухгалтер only").
        'purchase.payment.import.wizard': {
            'dept': False, 'buyer': False, 'chief_buyer': False,
            'accountant': True, 'keeper': False,
        },
        'purchase.payment.line': {
            'dept': False, 'buyer': False, 'chief_buyer': False,
            'accountant': True, 'keeper': False,
        },
        # Стеллажи/органайзеры склада - только Закупщик.
        'warehouse.rack.wizard': {
            'dept': False, 'buyer': True, 'chief_buyer': False,
            'accountant': False, 'keeper': False,
        },
        'warehouse.organizer.wizard': {
            'dept': False, 'buyer': True, 'chief_buyer': False,
            'accountant': False, 'keeper': False,
        },
    }

    # Получение товара (Подтвердить/Validate) - Закупщик, Кладовщик и
    # Главный закупщик могут писать в проводки склада, Бухгалтер и рядовой
    # сотрудник - только читают (см. коммит "Let Главный закупщик receive
    # goods themselves").
    WRITE_MATRIX = {
        'stock.picking': {
            'dept': False, 'buyer': True, 'chief_buyer': True,
            'accountant': False, 'keeper': True,
        },
        'stock.move': {
            'dept': False, 'buyer': True, 'chief_buyer': True,
            'accountant': False, 'keeper': True,
        },
        # stock.move.line - в отличие от stock.picking/stock.move, у него
        # уже есть НАТИВНАЯ строка ACL Odoo ("stock.move.line all users"),
        # дающая write+create всем Internal User независимо от наших ролей -
        # это стандартное поведение модуля stock, не наша дыра и трогать её
        # не нужно (реальная защита здесь - в отсутствии меню/кнопки, которые
        # рядовому сотруднику показали бы этот экран). Подтверждено 2026-07-10
        # при первом прогоне этого теста.
        'stock.move.line': {
            'dept': True, 'buyer': True, 'chief_buyer': True,
            'accountant': True, 'keeper': True,
        },
        # Исправление данных УПД после импорта (кнопка-карандаш на списке
        # "УПД") - те же роли, что уже имели write в ir.model.access.csv,
        # но это никогда не проверялось тестом до появления самой кнопки.
        'purchase.updd.line': {
            'dept': False, 'buyer': True, 'chief_buyer': True,
            'accountant': False, 'keeper': True,
        },
    }

    def test_create_access_matrix(self):
        for model, roles in self.CREATE_MATRIX.items():
            for role, expected in roles.items():
                with self.subTest(model=model, role=role):
                    self._assert_create_access(model, role, expected)

    def test_write_access_matrix(self):
        for model, roles in self.WRITE_MATRIX.items():
            for role, expected in roles.items():
                with self.subTest(model=model, role=role):
                    self._assert_write_access(model, role, expected)
