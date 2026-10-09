# Tender Hack Казань 2026

Кейс «Автозаполнение ключевых характеристик в СТЕ». Пользователь описывает товар
своими словами, а сервис находит конечный код КТРУ, заполняет характеристики в
унифицированном виде, показывает, как сужается выбор кодов, и объясняет каждую
найденную ошибку.

## Быстрый старт

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
```

## Подготовка КТРУ

Исходные ZIP кладутся в `КТРУ_raw/`. Сборщик берёт только
`nsiKTRUNew_all_actual_*.xml`, отбрасывает `not-actual` и оставляет последнюю версию
каждого кода. Дата проверяется по имени ZIP:

```bash
uv run python scripts/build_ktru_actual.py --expected-date 2026-10-05
```

Результат записывается в `data/processed/ktru_actual.jsonl.gz`. Одна строка содержит
полную позицию:

- `data` — код, версия, название, ОКПД2, статус, даты, единицы, классификаторы и
  характеристики;
- `printForm` — ссылка на печатную форму;
- `_source` — исходные ZIP и XML.

Файлы `data/` и `КТРУ_raw/` не попадают в Git.

## Загрузка data

`iter_data` читает файл построчно и возвращает только объект `data`. Параметр
`on_date` оставляет позиции со статусом `ACTIVE`, действующие на эту дату. Параметр
`expected_date` проверяет дату исходной выгрузки.

```python
from datetime import date

from scripts.dataloader import iter_data

data = iter_data(
    on_date=date(2026, 10, 10),
    expected_date=date(2026, 10, 5),
)
first_position = next(data)
```

Для загрузки всего набора в список доступна функция `load_data`. Для просмотра одной
позиции из командной строки:

```bash
uv run python scripts/dataloader.py \
  --date 2026-10-10 \
  --expected-date 2026-10-05 \
  --limit 1
```

## Документы

- docs/task.md — задача, критерии, ограничения, что сдаём
- docs/briefing.md — что говорили ведущие, по слайдам
- docs/data.md — данные и их устройство
