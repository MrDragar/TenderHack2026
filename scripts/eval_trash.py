"""Черновая проверка классификатора мусора на размеченном вручную наборе.

Набора с метками в проекте нет, поэтому метки выставлены здесь руками: ad/contact/latin/number
это мусор (True), настоящие слова-характеристики и товары это не мусор (False). Прогон нужен,
чтобы увидеть, где правило ошибается, и не притворяться, что точность измерена на золоте.
"""

from pipeline.trash import trash

TRASH = [
    "продам", "продаю", "продается", "продажа", "торг", "срочно", "срочный", "недорого",
    "дешево", "доставка", "самовывоз", "опт", "розница", "наличными", "договорная", "гарантия",
    "звоните", "звони", "пишите", "пиши", "обращайтесь", "авито", "нерабочий", "скидка",
    "распродажа", "бесплатно", "предоплата", "https://avito.ru/1", "www.shop.ru",
    "shop@mail.ru", "+79990001122", "manager@firma.com", "ikbavfibyf", "42", "123456", "xyz",
]

CLEAN = [
    "трактор", "шины", "ударом", "рабочие", "хлопок", "ящиком", "столешница", "деревянный",
    "металлический", "размер", "цвет", "вес", "материал", "назначение", "высота", "длина",
    "ширина", "глубина", "мощность", "диаметр", "объем", "покрытие", "наполнение", "каркас",
    "ABS", "EVA", "USB", "XL", "IP67", "220V", "5000mAh",
]


def score(samples: list[str], positive: bool) -> tuple[int, int]:
    hit = sum(trash(s) == positive for s in samples)
    return hit, len(samples)


def main() -> None:
    tp, pt = score(TRASH, True)
    tn, nt = score(CLEAN, False)
    fp = pt - tp
    fn = nt - tn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    print(f"полнота (recall) мусора:      {tp}/{pt} = {recall:.2f}")
    print(f"точность (precision) мусора:  {tp}/{tp + fp} = {precision:.2f}")
    print(f"F1:                           {f1:.2f}")
    print(f"настоящие слова уцелели:      {tn}/{nt} = {tn / nt:.2f}")
    print("--- ошибки ---")
    for s in TRASH:
        if not trash(s):
            print(f"  пропущен мусор: {s!r}")
    for s in CLEAN:
        if trash(s):
            print(f"  назван мусором: {s!r}")


if __name__ == "__main__":
    main()
