from odoo.tests.common import BaseCase, tagged

from ..wizard.inn_utils import is_valid_inn, normalize_inn

# Реальные ИНН из боевой базы на 22.07.2026 (см. work/analytics_reference.md
# и п. 9.1 ТЗ - "16 записей по 10 цифр (юрлица), 6 по 12 (ИП) - все
# корректны"). Взяты буквально, чтобы тест проверял алгоритм на настоящих
# данных, а не на придуманных числах, которые могут случайно оказаться
# невалидными или наоборот.
REAL_LEGAL_ENTITY_INNS = [
    '2222852056', '4704114940', '7704217370', '7713499459', '7713788154',
    '7722753969', '7802691027', '7804155511', '7806267789', '7806513674',
    '7814458770', '7814673738', '7814767672', '7814791530', '9727077690',
]
REAL_SOLE_PROPRIETOR_INNS = [
    '026811266534', '263201460984', '400402613329', '434592468031',
    '745110565279', '780539051541',
]
# '7801234567' (ООО "Микрон Электро") специально НЕ в списке выше: это
# реальное значение из прода, но оно не проходит контрольную сумму (похоже
# на плейсхолдер "1234567", введённый до появления какой-либо проверки).
# Используется ниже как настоящий, а не придуманный, пример брака в данных.
REAL_INVALID_LOOKING_INN = '7801234567'


@tagged('post_install', '-at_install')
class TestInnUtils(BaseCase):

    def test_real_legal_entity_inns_are_valid(self):
        for inn in REAL_LEGAL_ENTITY_INNS:
            self.assertTrue(is_valid_inn(inn), f'{inn} должен проходить контрольную сумму (юрлицо)')

    def test_real_sole_proprietor_inns_are_valid(self):
        for inn in REAL_SOLE_PROPRIETOR_INNS:
            self.assertTrue(is_valid_inn(inn), f'{inn} должен проходить контрольную сумму (ИП)')

    def test_real_data_defect_is_correctly_rejected(self):
        # Не выдумываем брак - берём реальный испорченный ИНН из прода и
        # убеждаемся, что валидатор его действительно бракует, а не
        # случайно пропускает.
        self.assertFalse(is_valid_inn(REAL_INVALID_LOOKING_INN))

    def test_broken_checksum_digit_rejected(self):
        good = REAL_LEGAL_ENTITY_INNS[0]
        broken = good[:-1] + str((int(good[-1]) + 1) % 10)
        self.assertFalse(is_valid_inn(broken))

        good12 = REAL_SOLE_PROPRIETOR_INNS[0]
        broken12 = good12[:-1] + str((int(good12[-1]) + 1) % 10)
        self.assertFalse(is_valid_inn(broken12))

    def test_wrong_length_rejected(self):
        self.assertFalse(is_valid_inn(''))
        self.assertFalse(is_valid_inn('123'))
        self.assertFalse(is_valid_inn('1' * 11))  # 11 цифр - не бывает

    def test_non_digit_rejected(self):
        self.assertFalse(is_valid_inn('абвгдежзик'))
        self.assertFalse(is_valid_inn(None))
        self.assertFalse(is_valid_inn(False))

    def test_normalize_strips_everything_but_digits(self):
        self.assertEqual(normalize_inn('7710 01001'), '771001001')
        self.assertEqual(normalize_inn('ИНН 7710010019'), '7710010019')
        self.assertEqual(normalize_inn(''), '')
        self.assertEqual(normalize_inn(None), '')
        self.assertEqual(normalize_inn('7814791530'), '7814791530')

    def test_normalize_then_validate_recovers_real_inn(self):
        # Тот самый сценарий из п. 9.2 ТЗ: ИИ дописал пробел или префикс -
        # после нормализации проверка контрольной суммы должна снова пройти.
        messy = 'ИНН ' + REAL_LEGAL_ENTITY_INNS[3]
        self.assertTrue(is_valid_inn(normalize_inn(messy)))
