"""Нормализация и проверка ИНН (контрольная сумма) для сопоставления
поставщиков. Чистые функции без ORM - используются и мастерами импорта,
и post_init_hook, и юнит-тестами напрямую."""


def normalize_inn(value):
    """Оставляет только цифры. '' для пустого/нецифрового значения."""
    if not value:
        return ''
    return ''.join(ch for ch in str(value) if ch.isdigit())


def is_valid_inn(value):
    """Проверка контрольной суммы ИНН РФ. 10 цифр - юрлицо, 12 - ИП."""
    digits = normalize_inn(value)
    if len(digits) == 10:
        weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
        d = [int(c) for c in digits]
        checksum = sum(d[i] * weights[i] for i in range(9)) % 11 % 10
        return checksum == d[9]
    if len(digits) == 12:
        d = [int(c) for c in digits]
        weights1 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        checksum1 = sum(d[i] * weights1[i] for i in range(10)) % 11 % 10
        weights2 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        checksum2 = sum(d[i] * weights2[i] for i in range(11)) % 11 % 10
        return checksum1 == d[10] and checksum2 == d[11]
    return False
