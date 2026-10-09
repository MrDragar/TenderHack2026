import json
import sys
from functools import cache

from contracts.pipeline import ExtractChars, FindCandidates, Narrow, Result
from contracts.ref import Position
from pipeline.candidates import find
from pipeline.chars import extract
from pipeline.narrow import narrow
from scripts.dataloader import load_data

find_candidates: FindCandidates = find
extract_chars: ExtractChars = extract
narrow_codes: Narrow = narrow


@cache
def reference() -> list[Position]:
    return load_data()


def run(text: str) -> Result:
    ref = reference()
    found = find_candidates(text, ref)
    return narrow_codes(found, extract_chars(text, found, ref, {}), ref)


if __name__ == "__main__":
    print(json.dumps(run(" ".join(sys.argv[1:])), ensure_ascii=False, indent=2))
