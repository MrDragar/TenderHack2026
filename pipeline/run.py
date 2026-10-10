import json
import sys
from functools import cache

from contracts.pipeline import ExtractChars, FindCandidates, Narrow, Result
from contracts.ref import Position
from pipeline.candidates import find
from pipeline.chars import extract
from pipeline.narrow import narrow
from scripts.dataloader import load_data


@cache
def reference() -> list[Position]:
    return load_data()


def run(
    text: str,
    find: FindCandidates = find,
    extract: ExtractChars = extract,
    narrow: Narrow = narrow,
) -> Result:
    """Этапы подменяются вариантом или эталоном, так замер сравнивает их по отдельности."""
    ref = reference()
    found = find(text, ref)
    return narrow(found, extract(text, found, ref, {}), ref)


if __name__ == "__main__":
    print(json.dumps(run(" ".join(sys.argv[1:])), ensure_ascii=False, indent=2))
