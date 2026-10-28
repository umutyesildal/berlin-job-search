from __future__ import annotations

import re


def select_marked_blocks(text: str, disabled_sources: set[str], suffix: str) -> str:
    selected = "linkedin-links" if "linkedin" in disabled_sources else "linkedin-guest"
    if suffix == ".py":
        pattern = re.compile(
            r"(?m)^[ \t]*#\s*(linkedin-guest|linkedin-links):start\s*\n"
            r"(.*?)^[ \t]*#\s*\1:end\s*(?:\n|$)",
            re.DOTALL,
        )
    else:
        pattern = re.compile(
            r"<!--\s*(linkedin-guest|linkedin-links):start\s*-->"
            r"(.*?)<!--\s*\1:end\s*-->",
            re.DOTALL,
        )

    def replace(match: re.Match[str]) -> str:
        if match.group(1) != selected:
            return ""
        content = match.group(2)
        if suffix != ".py":
            stripped = content.strip()
            if stripped.startswith("<!--") and stripped.endswith("-->"):
                return stripped[4:-3].strip()
        return content

    result = pattern.sub(replace, text)
    marker = r"(?:linkedin-guest|linkedin-links):(?:start|end)"
    if re.search(marker, result):
        raise ValueError("unmatched LinkedIn build marker")
    return result
