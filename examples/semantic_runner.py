import importlib
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from contracts.ref import Characteristic, Portal, Position, Value
from pipeline import memory
from pipeline.semantic import run


def text_char(
    code: str,
    name: str,
    values: Sequence[Value],
    *,
    required: bool = False,
    selection: Literal["fixed", "single", "multiple"] = "single",
) -> Characteristic:
    item: Characteristic = {
        "code": code,
        "name": name,
        "type": "text",
        "selection": selection,
        "required": required,
        "values": list(values),
    }
    return item


HEIGHT: Characteristic = {
    "code": "height",
    "name": "Высота",
    "type": "number",
    "selection": "single",
    "required": True,
    "values": [{"min": 700, "min_inclusive": True, "max": 800, "max_inclusive": True}],
    "unit": {"code": "003", "name": "Миллиметр"},
}
WIDTH: Characteristic = {
    "code": "width",
    "name": "Ширина",
    "type": "number",
    "selection": "single",
    "required": True,
    "values": [1200],
    "unit": {"code": "003", "name": "Миллиметр"},
}


def desk(code: str, frame: str) -> Position:
    return {
        "code": code,
        "name": "Стол письменный",
        "category": "furniture",
        "okpd2_code": "31.01.10",
        "position_type": "final",
        "parent_code": "desk",
        "units": [],
        "characteristics": [
            text_char("frame", "Тип каркаса", [frame], required=True, selection="fixed"),
            HEIGHT,
            WIDTH,
        ],
    }


POSITIONS: list[Position] = [
    desk("wood", "Деревянный"),
    desk("metal", "Металлический"),
    {
        "code": "chair",
        "name": "Кресло офисное",
        "category": "furniture",
        "okpd2_code": "31.01.11",
        "position_type": "final",
        "parent_code": "chair-family",
        "units": [],
        "characteristics": [
            text_char("base", "Тип основания", ["На колесах"], required=True, selection="fixed")
        ],
    },
]
PORTAL: Portal = {
    "Столы письменные": [
        {"name": "Цвет", "type": "Справочник", "unit": None, "values": ["Серый", "Белый"]},
        {"name": "Кабель-канал", "type": "Текст", "unit": None, "values": []},
    ],
    "Кресла офисные": [
        {"name": "Цвет", "type": "Справочник", "unit": None, "values": ["Серый", "Черный"]}
    ],
    "Органайзеры для инструментов": [
        {"name": "Цвет", "type": "Справочник", "unit": None, "values": ["Красный"]},
        {"name": "Материал", "type": "Справочник", "unit": None, "values": ["Пластик"]},
    ],
}


class DemoRunner:
    def __init__(self):
        self.temporary: TemporaryDirectory | None = None
        self.memory_path: Path | None = None

    def begin_scenario(self, scenario) -> None:
        self.end_scenario()
        self.temporary = TemporaryDirectory()
        self.memory_path = Path(self.temporary.name) / "learned.sqlite3"

    def restart(self) -> None:
        importlib.reload(memory)

    def end_scenario(self) -> None:
        if self.temporary:
            self.temporary.cleanup()
        self.temporary = None
        self.memory_path = None

    def __call__(self, text: str, learn: bool):
        if self.memory_path is None:
            self.begin_scenario({})
        memory_path = self.memory_path
        assert memory_path is not None
        return run(
            text,
            learn,
            memory_path=memory_path,
            positions=POSITIONS,
            portal=PORTAL,
        )


runner = DemoRunner()
