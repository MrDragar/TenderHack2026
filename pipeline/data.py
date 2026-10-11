from functools import cache

from contracts.ref import Portal, Position
from scripts.dataloader import DEFAULT_PORTAL_PATH, load_data, load_portal


@cache
def reference() -> list[Position]:
    return load_data()


@cache
def portal() -> Portal:
    # Без собранного листа 2 работаем на одном КТРУ, а не падаем.
    if not DEFAULT_PORTAL_PATH.exists():
        return {}
    return load_portal()
