"""Snapshot commitment: a sha256 over a table's rows in a canonical form.

The same function exists in the browser (``web/src/lib/commitment.ts``); the
two MUST stay byte-identical. Canonical form, per value:

  - bool stays bool;
  - int / float: finite and integral -> integer, finite otherwise -> the float;
    non-finite (NaN, +/-inf) -> null (the JSON response serialises them as null,
    so that is what the browser sees);
  - None -> null;
  - anything else -> its string form. Non-primitive values are first converted
    the way the API's JSON serialiser converts them (dates -> ISO strings,
    Decimal -> string), again so both sides hash what is actually served.

Each row (a list, columns in bundle order) is serialised as compact JSON
(Python ``json.dumps(row, separators=(",", ":"), ensure_ascii=False)``, TS
``JSON.stringify(row)``), the row strings are sorted (plain string sort, so
row order does not matter), joined with ``"\\n"`` and hashed as UTF-8.

Known portability edge cases (the hashes can disagree even though the rows are
the same):

  - numbers whose shortest representation needs exponent notation differ
    between Python and JS (``1e-07`` vs ``1e-7``), and integral values with
    magnitude >= 1e21 print in full in Python but as ``1e+21`` in JS; integers
    beyond 2**53 also lose precision when parsed in the browser;
  - sort order: Python sorts by code point, JS by UTF-16 code unit, so rows
    whose first difference involves an astral-plane character (e.g. emoji)
    against a character in U+E000..U+FFFF may sort differently;
  - nested lists/dicts inside a cell are stringified differently by each side.

The demo and finance schemas (text, dates, modest decimals) avoid all of these.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Sequence
from typing import Any

from pydantic_core import to_jsonable_python


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        f = float(value)
        if not math.isfinite(f):
            return None
        if isinstance(value, int):
            return value
        return int(value) if value.is_integer() else value
    if isinstance(value, str):
        return value
    served = to_jsonable_python(value)
    return served if isinstance(served, str) else str(value)


def canonical_rows(rows: Iterable[Sequence[Any]]) -> list[str]:
    """Each row as its canonical compact-JSON string, sorted."""
    return sorted(
        json.dumps([_canonical_value(v) for v in row], separators=(",", ":"), ensure_ascii=False)
        for row in rows
    )


def commit_rows(rows: Iterable[Sequence[Any]]) -> str:
    """sha256 (hex) of the canonical form of ``rows``."""
    return hashlib.sha256("\n".join(canonical_rows(rows)).encode("utf-8")).hexdigest()
