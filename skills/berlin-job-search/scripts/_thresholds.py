from __future__ import annotations

import datetime as dt
import json
from functools import lru_cache
from pathlib import Path
from typing import Any


DATA_PATH = Path(__file__).resolve().parent.parent / "assets" / "thresholds.json"
VISA_KEYS = (
    "blue_card_regular",
    "blue_card_shortage_or_graduate",
    "it_specialist_no_degree_6_beschv",
    "age_45_plus_min",
)
REQUIRED_KEYS = frozenset((*VISA_KEYS, "chancenkarte", "minimum_wage", "minijob"))


@lru_cache(maxsize=1)
def load_thresholds() -> list[dict[str, Any]]:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def official_amounts(as_of: dt.date) -> dict[str, float]:
    amounts = {}
    for row in sorted(load_thresholds(), key=lambda row: row["effective_from"]):
        effective = dt.date.fromisoformat(row["effective_from"])
        if effective.year == as_of.year and effective <= as_of and row["status"] == "official":
            amounts[row["key"]] = row["amount"]
    return amounts


def render_reference() -> str:
    lines = [
        "# German salary, wage and financing thresholds",
        "",
        "Annual reference figures. **Calculated** rows are for planning until confirmed by the "
        "competent authority. An official row with a future effective date does not apply yet.",
        "",
        "<!-- Refresh with: python3 skills/berlin-job-search/scripts/_thresholds.py -->",
        "",
        "| Effective from | Threshold | Amount | Status | Source |",
        "|---|---|---:|---|---|",
    ]
    for row in load_thresholds():
        amount = row["amount"]
        money = "{:,.2f}" if row["unit"] == "gross/hour" or amount % 1 else "{:,.0f}"
        status = row["status"] + (": " + row["basis"] if row.get("basis") else "")
        lines.append("| {} | {} | €{} {} | {} | [Source]({}) |".format(
            row["effective_from"], row["label"], money.format(amount), row["unit"], status, row["source_url"],
        ))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(render_reference(), end="")
