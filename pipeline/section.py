from contracts.ref import Portal
from pipeline.text import best_named


def find_section(text: str, portal: Portal) -> str | None:
    """Из равных по совпадению берём первый по алфавиту, чтобы ответ не зависел от порядка."""
    names = sorted(portal)
    top = best_named(text, names, names)
    return top[0] if top else None
