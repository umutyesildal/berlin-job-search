from __future__ import annotations

import re
from typing import MutableSet


def _block_pattern(suffix: str) -> re.Pattern[str]:
    if suffix == ".py":
        return re.compile(
            r"(?m)^[ \t]*#\s*linkedin-guest:start\s+id=([A-Za-z0-9_-]+)[ \t]*\r?\n"
            r"(.*?)^[ \t]*#\s*linkedin-guest:end\s+id=\1[ \t]*(?:\r?\n|$)",
            re.DOTALL,
        )
    return re.compile(
        r"<!--\s*linkedin-guest:start\s+id=([A-Za-z0-9_-]+)\s*-->"
        r"(.*?)<!--\s*linkedin-guest:end\s+id=\1\s*-->",
        re.DOTALL,
    )


def _marker_pattern(suffix: str) -> re.Pattern[str]:
    if suffix == ".py":
        return re.compile(
            r"(?m)^[ \t]*#\s*linkedin-guest:(?:start|end)\s+id=[A-Za-z0-9_-]+[ \t]*(?:\r?\n|$)"
        )
    return re.compile(
        r"<!--\s*linkedin-guest:(?:start|end)\s+id=[A-Za-z0-9_-]+\s*-->"
    )


def _check_markers(text: str) -> None:
    if re.search(r"linkedin-guest:(?:start|end)", text):
        raise ValueError("unmatched or invalid LinkedIn guest marker")


def apply_guest_overrides(
    text: str,
    overrides: dict[str, str],
    suffix: str,
    used: MutableSet[str],
) -> str:
    pattern = _block_pattern(suffix)

    def replace(match: re.Match[str]) -> str:
        block_id = match.group(1)
        if block_id not in overrides:
            raise ValueError("missing OpenAI override for LinkedIn block {!r}".format(block_id))
        if block_id in used:
            raise ValueError("duplicate LinkedIn block id {!r}".format(block_id))
        used.add(block_id)
        return overrides[block_id]

    result = pattern.sub(replace, text)
    _check_markers(result)
    return result


def strip_guest_markers(text: str, suffix: str) -> str:
    result = _marker_pattern(suffix).sub("", text)
    _check_markers(result)
    return result
