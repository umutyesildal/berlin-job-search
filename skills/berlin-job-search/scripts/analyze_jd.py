from __future__ import annotations

import argparse
import datetime as dt
import html
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

from _common import json_text, strip_html, write_output
from _thresholds import VISA_KEYS, official_amounts

GERMAN_STOPWORDS = set(
    "aber als am an auch auf aus bei das dass dem den der des die doch durch ein eine "
    "einer einem einen für gegen hat im in ist mit nach nicht oder sein sie sind und von "
    "vor wie zu zum zur".split()
)
ENGLISH_STOPWORDS = set(
    "a about after all also an and any are as at be because been before being between both "
    "but by can could did do does doing for from had has have he her here him his how i if "
    "in into is it its may more most my no not of on or our out over she should so some than "
    "that the their them then there these they this to up was we were what when where which "
    "who will with would you your".split()
)

REQUIRED_GERMAN = [
    re.compile(pattern, re.I)
    for pattern in (
        r"\b(?:fließende|fliessende|fließend|fliessend|gute|sehr gute|ausgezeichnete|fundierte|verhandlungssichere)\s+deutschkenntnisse\b",
        r"\bdeutschkenntnisse\s+(?:sind\s+)?(?:fließend|fliessend)\b",
        r"\b(?:gute|sehr gute|ausgezeichnete|fundierte|verhandlungssichere|fließende|fliessende)\s+kenntnisse\s+(?:in|der)\s+deutsch(?:en)?(?:\s+sprache)?\b",
        r"\bdeutsch\s+(?:fließend|fliessend)\b",
        r"\b(?:fließend|fliessend)\s+deutsch\b",
        r"\b(?:fließend|fliessend)\s+in\s+deutsch(?:\s+und\s+englisch)?(?:\s*\([^)]*\))?",
        r"\bdeutsch\s+in\s+wort\s+und\s+schrift\b",
        r"\b(?:sehr\s+)?gute[sn]?\s+deutsch\b(?![-\w])",
        r"\bspeaks?\s+german\s+fluently\b",
        r"\bfluent\s+(?:in\s+)?german\b",
        r"\bfluent\s+in\s+english\s+and\s+german\b",
        r"\bfluency\s+in\s+(?:(?:english)\s+and\s+)?german(?:\s+and\s+english)?\b",
        r"\bfluent\s+german\s+and\s+english\b",
        r"\b(?:excellent|strong|very good)\s+(?:written\s+and\s+verbal\s+)?"
        r"(?:communication\s+)?skills\s+in\s+english\s+and\s+german\b",
        r"\b(?:sehr\s+)?gute[nr]?\s+deutsch[- ]kenntnisse\b",
        r"\bdeutsch[- ]kenntnisse\s*\(\s*(?:mind(?:estens)?\.?\s*)?(?:b1|b2|c1|c2)\+?\s*\)",
        r"\b(?:fließende|fliessende|gute[nr]?|sehr gute[nr]?|ausgezeichnete[nr]?|fundierte[nr]?|"
        r"verhandlungssichere[nr]?)\s+deutsch\s*-\s+und\s+(?:gute[nr]?\s+)?englischkenntnisse\b",
        r"\b(?:very\s+good|excellent|strong|proficient)\s+(?:level\s+of\s+)?german\b",
        r"\bdeutsch\s+und\s+englisch\s+auf\s+c[12]\s+bzw\.?\s+c[12](?:[- ]?niveau)\b",
        r"\bproficiency\s+in\s+german\b",
        r"\bdeutschkenntnisse\s*\(\s*mind(?:estens)?\.?\s*(?:b2|c1|c2)\+?\s*\)",
        r"\bdeutschkenntnisse\s+mind(?:estens)?\.?\s*(?:b2|c1|c2)\+?\b",
        r"\bdeutsch(?:kenntnisse)?\s*(?:\(\s*)?(?:b2|c1|c2)\+?(?:[- ]level)?\s*\)?(?=\W|$)",
        r"\bdeutsch\s+auf\s+(?:dem\s+)?(?:b2|c1|c2)\+?[- ]niveau\b",
        r"\bgerman\s*(?:\(\s*)?(?:b2|c1|c2)\+?(?:[- ]level)?\s*\)?(?=\W|$)",
        r"\bgerman\s+(?:at|on)\s+(?:the\s+)?(?:b2|c1|c2)\+?\s+level\b",
        r"\bgerman\s+language\s+skills?\s*\(\s*(?:b2|c1|c2)\+?(?:[- ]level)?\s*\)",
        r"\bgerman\s+(?:is\s+)?a\s+must\b",
        r"\bnative\s+(?:level\s+)?german\b",
        r"\bdeutsch(?:kenntnisse)?\s+(?:auf\s+)?(?:muttersprachlichem|muttersprachler)\w*\s+niveau\b",
        r"\bsehr gute kenntnisse der deutschen sprache\b",
    )
]
NICE_GERMAN = [
    re.compile(pattern, re.I)
    for pattern in (
        r"\bgerman(?:\s+language)?(?:\s+skills?)?\s+(?:is|are)?\s*(?:(?:a|an)\s+)?(?:plus|bonus|advantage|advantageous|nice to have|preferred|desirable)\b",
        r"\bdeutschkenntnisse\s+(?:sind\s+)?(?:von vorteil|wünschenswert|wuenschenswert|ein plus)\b",
        r"\bdeutsch\s+(?:ist\s+)?(?:ein\s+)?(?:plus|vorteil|bonus|wünschenswert|wuenschenswert)\b",
    )
]
NOT_REQUIRED_GERMAN = [
    re.compile(pattern, re.I)
    for pattern in (
        r"\bno german required\b",
        r"\bgerman\s+(?:is\s+)?not required\b",
        r"\bkeine deutschkenntnisse erforderlich\b",
        r"\bdeutschkenntnisse sind nicht erforderlich\b",
        r"\benglish is our\s+(?:working|company)\s+language\b",
        r"\benglish is the (?:working|company) language\b",
    )
]

GERMAN_QUALIFIER = re.compile(
    r"\b(?:a\s+plus|plus|bonus|advantage|advantageous|nice\s+to\s+have|preferred|"
    r"desirable|von\s+vorteil|wünschenswert|wuenschenswert|ein\s+plus)\b(?!\s*:)",
    re.I,
)
SENTENCE_BREAK = re.compile(r"[.!?;\n]")
EVIDENCE_BREAK = re.compile(r"[.!?;\n•]")
LANGUAGE_NOUN = r"(?:german|english|deutsch\w*|englisch\w*)"
GERMAN_CLAUSE_BREAK = re.compile(
    r"[,;.!?\n•]|\b(?:und|and)\b(?=\s+" + LANGUAGE_NOUN + r"\b)",
    re.I,
)
TITLE_GERMAN_REQUIRED = [
    re.compile(pattern, re.I)
    for pattern in (
        r"\bgerman[- ]speaker\b",
        r"\bwith\s+german\b",
        r"\bgerman[- ]speaking\b",
        r"\bdeutschsprachig\w*\b",
    )
]
AGENCY_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in (
        r"\barbeitsvermittlung\w*\b",
        r"\bpersonalvermittlung\w*\b",
        r"\bpersonaldienstleist\w*\b",
        r"\bpersonalberatung\w*\b",
        r"\bzeitarbeit\b",
        r"\barbeitnehmer(?:überlassung|ueberlassung)\b",
        r"\bim\s+auftrag\s+unseres\s+kunden\b",
        r"\b(?:für|fuer)\s+unseren\s+kunden\b",
        r"\bunser\s+kunde\b",
        r"\bunserem\s+kunden\b",
        r"\bour\s+client\b",
        r"\bon\s+behalf\s+of\s+our\s+client\b",
        r"\[\s*arbeitgeber\s*\]",
    )
]

MONEY_NUMBER = r"(?<![A-Za-z])(?<!\d)(?:\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
MONEY_TOKEN = r"(?:€\s*)?" + MONEY_NUMBER + r"\s*k?(?![A-Za-z])\s*(?:€|euro|eur)?"
RANGE_PATTERNS = (
    re.compile(r"\bzwischen\s+(?P<left>" + MONEY_TOKEN + r")\s+und\s+(?P<right>" + MONEY_TOKEN + r")", re.I),
    re.compile(r"\bfrom\s+(?P<left>" + MONEY_TOKEN + r")\s+to\s+(?P<right>" + MONEY_TOKEN + r")", re.I),
    re.compile(r"\bbetween\s+(?P<left>" + MONEY_TOKEN + r")\s+and\s+(?P<right>" + MONEY_TOKEN + r")", re.I),
    re.compile(r"\bvon\s+(?P<left>" + MONEY_TOKEN + r")\s+bis\s+(?P<right>" + MONEY_TOKEN + r")", re.I),
    re.compile(r"(?P<left>" + MONEY_TOKEN + r")\s*(?:-|–|—|bis)\s*(?P<right>" + MONEY_TOKEN + r")", re.I),
)
SINGLE_RE = re.compile(MONEY_TOKEN, re.I)
PAY_CONTEXT = re.compile(
    r"\b(?:salary|gehalt|jahresgehalt|monatsgehalt|vergütung|verguetung|verdienst|"
    r"bezahlung|lohn|stundenlohn|entgelt|compensation|pay|brutto|gross|base|range|ote)\b"
    r"|(?:per\s+hour|an\s+hour|hourly|pro\s+stunde|/h\b|/stunde\b)",
    re.I,
)
NON_SALARY_ITEM = re.compile(
    r"\b(?:deutschlandticket|jobticket|ticket|zuschuss|subsidy|budget|allowance|voucher|"
    r"gutschein|jobrad|urlaubsgeld|urlaub|vacation)\b"
    r"|\b\d+\s*(?:days?|tage?)\s+(?:of\s+)?(?:vacation|urlaub)\b"
    r"|\b\d+\s*%|%\s*\d+",
    re.I,
)
PAY_PERIODS = {
    "hour": re.compile(r"\b(?:per hour|an hour|hourly|stunde|stundenlohn|pro stunde)\b|/\s*(?:h|hour|stunde)\b"),
    "month": re.compile(r"\b(?:per month|monthly|monatlich|pro monat|im monat|monatsgehalt)\b|/\s*(?:month|monat)\b"),
    "year": re.compile(r"\b(?:per year|a year|annual(?:ly)?|yearly|jährlich|jaehrlich|jahr|jahresgehalt)\b|/\s*(?:year|jahr)\b"),
}
WEEKLY_HOURS = re.compile(
    r"(?<![\d.,])\b(?P<qualifier>(?:up to|bis zu|at least|mindestens|max(?:imal)?\.?|ca\.?|"
    r"around|about|approx(?:imately)?\.?)\s+)?(?P<hours>\d+(?:[.,]\d+)?)"
    r"(?P<range>\s*(?:[-–—/]|to|bis|or|oder)\s*\d+(?:[.,]\d+)?)?\s*"
    r"(?:(?:hours?|hrs?|h|stunden)(?:\s+(?:per|a|pro|je)\s+|\s*/\s*|\s+)(?:week|woche)|wochenstunden)\b"
)
UNCERTAIN_PAY_BASIS = re.compile(
    r"\b(?:net(?:to)?|after tax(?:es)?|nach steuern|fte|full[- ]time equivalent|full[- ]time basis|"
    r"based on full[- ]time|vollzeitbasis|bei vollzeit|anteilig|pro[- ]rata|"
    r"on[- ]target earnings|ote|total compensation|gesamtvergütung|bonus|boni|commission|provision|"
    r"up to|bis zu)\b"
)
FULL_TIME = re.compile(r"\b(?:full[- ]time|vollzeit)\b")
PART_TIME = re.compile(r"\b(?:part[- ]time|teilzeit)\b", re.I)
INSTRUCTION_PATTERNS = (
    re.compile(r"\bignore\s+(?:(?:all|any)\s+)?(?:(?:previous|prior|the above)\s+)?instructions\b", re.I),
    re.compile(
        r"\byou\s+are\s+(?:an?|the)\s+(?:ai(?!\s+(?:engineer|developer|researcher|scientist|"
        r"architect|specialist|lead|consultant|manager|analyst|intern|trainer|expert|strategist|"
        r"designer|tester|owner|programmer)\b)|assistant|language\s+model|llm)\b",
        re.I,
    ),
    re.compile(
        r"\bif\s+you\s+are\s+an?\s+(?:ai(?!\s+(?:engineer|developer|researcher|scientist|"
        r"architect|specialist|lead|consultant|manager|analyst|intern|trainer|expert|strategist|"
        r"designer|tester|owner|programmer)\b)|llm|language\s+model|assistant)\b",
        re.I,
    ),
    re.compile(r"\b(?:ai|llm)\s+(?:agents?|assistants?|models?)\s+(?:must|should|need\s+to)\b", re.I),
    re.compile(r"\bsystem\s+prompt\b", re.I),
    re.compile(r"\brate\s+(?:this|the)\s+(?:candidate|application|applicant|job)\s+(?:as|with)\b", re.I),
    re.compile(r"\bignoriere\s+(?:alle\s+)?(?:(?:vorherigen|bisherigen)\s+)?anweisungen\b", re.I),
    re.compile(r"\bwenn\s+du\s+eine\s+ki\s+bist\b", re.I),
)
INSTRUCTION_MARKERS = re.compile(r"<\|im_start\|>|\[INST\]", re.I)


class IntegrityHTMLParser(HTMLParser):
    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }
    SKIP_TAGS = {"script", "style", "template", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[dict[str, Any]] = []
        self.all_text: list[str] = []
        self.visible_text: list[str] = []
        self.hidden_issues: list[dict[str, Any]] = []
        self.link_issues: list[dict[str, Any]] = []

    @staticmethod
    def _hidden_by_attributes(attrs: dict[str, str]) -> bool:
        if "hidden" in attrs or attrs.get("aria-hidden", "").strip().casefold() == "true":
            return True
        styles = {}
        for declaration in attrs.get("style", "").split(";"):
            name, separator, value = declaration.partition(":")
            if separator:
                styles[name.strip().casefold()] = value.split("!", 1)[0].strip().casefold()
        if styles.get("display") == "none" or styles.get("visibility") == "hidden":
            return True
        font_size = styles.get("font-size", "")
        if re.fullmatch(r"0(?:\.0+)?(?:px|pt|em|rem|%)?", font_size):
            return True
        try:
            if float(styles.get("opacity", "1")) == 0:
                return True
        except ValueError:
            pass
        return False

    def _active_context(self, name: str) -> Any:
        for frame in reversed(self.stack):
            if frame.get(name) is not None:
                return frame[name]
        return None

    @staticmethod
    def _normalized_text(chunks: list[str]) -> str:
        return re.sub(r"\s+", " ", " ".join(chunks)).strip()

    def _finish_frame(self, frame: dict[str, Any]) -> None:
        hidden_owner = frame.get("hidden_owner")
        if hidden_owner is not None:
            hidden_text = self._normalized_text(hidden_owner["chunks"])
            if len(hidden_text) >= 20:
                self.hidden_issues.append({
                    "type": "hidden_html",
                    "evidence": {
                        "tag": hidden_owner["tag"],
                        "chars": len(hidden_text),
                        "text": hidden_text[:120],
                    },
                })
        anchor = frame.get("anchor")
        if anchor is not None and anchor.get("has_href"):
            anchor_text = self._normalized_text(anchor["chunks"])
            if anchor["hidden"] or not anchor_text:
                reason = "inside a hidden container" if anchor["hidden"] else "no visible text"
                self.link_issues.append({
                    "type": "hidden_link",
                    "evidence": {"href": anchor["href"], "reason": reason},
                })

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag = tag.casefold()
        attr_map = {name.casefold(): value or "" for name, value in attrs}
        parent_skip = any(frame["skip"] for frame in self.stack)
        skip = parent_skip or tag in self.SKIP_TAGS
        parent_hidden = None if skip else self._active_context("hidden_context")
        own_hidden = not skip and self._hidden_by_attributes(attr_map)
        hidden_owner = None
        hidden_context = parent_hidden
        if own_hidden and parent_hidden is None:
            hidden_owner = {"tag": tag, "chunks": []}
            hidden_context = hidden_owner
        elif own_hidden:
            hidden_context = parent_hidden
        anchor = None
        if not skip and tag == "a":
            anchor = {
                "href": attr_map.get("href", ""),
                "has_href": "href" in attr_map,
                "hidden": bool(parent_hidden or own_hidden),
                "chunks": [],
            }
        frame = {
            "tag": tag,
            "skip": skip,
            "hidden_context": hidden_context,
            "hidden_owner": hidden_owner,
            "anchor": anchor,
        }
        if tag in self.VOID_TAGS:
            self._finish_frame(frame)
            return
        self.stack.append(frame)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                closing = self.stack[index:]
                del self.stack[index:]
                for frame in reversed(closing):
                    self._finish_frame(frame)
                return

    def handle_data(self, data: str) -> None:
        if any(frame["skip"] for frame in self.stack):
            return
        self.all_text.append(data)
        hidden_context = self._active_context("hidden_context")
        if hidden_context is not None:
            hidden_context["chunks"].append(data)
        else:
            self.visible_text.append(data)
        anchor = self._active_context("anchor")
        if anchor is not None and not anchor["hidden"] and hidden_context is None:
            anchor["chunks"].append(data)

    def close(self) -> None:
        super().close()
        for frame in reversed(self.stack):
            self._finish_frame(frame)
        self.stack.clear()


def clean_text(text: str) -> str:
    if re.search(r"<\s*(?:html|body|div|p|section|ul|li)\b", text, re.I):
        text = strip_html(text)
    return re.sub(r"\s+", " ", text).strip()


def integrity_of(raw_text: str) -> dict[str, Any]:
    raw = html.unescape(raw_text or "")
    zero_width_joiners = sum(ord(char) in {0x200C, 0x200D} for char in raw)
    ignored_joiners = min(zero_width_joiners, 2)
    zero_width_format = sum(
        0x200B <= ord(char) <= 0x200F and ord(char) not in {0x200C, 0x200D}
        or 0x2060 <= ord(char) <= 0x2064
        or ord(char) == 0xFEFF
        for char in raw
    ) + max(0, zero_width_joiners - 2)
    bidi_controls = sum(
        0x202A <= ord(char) <= 0x202E or 0x2066 <= ord(char) <= 0x2069
        for char in raw
    )
    tag_characters = sum(0xE0000 <= ord(char) <= 0xE007F for char in raw)
    issues = []
    if zero_width_format or bidi_controls or tag_characters:
        issues.append({
            "type": "invisible_chars",
            "evidence": {
                "zero_width_format": zero_width_format,
                "zwj_zwnj_ignored": ignored_joiners,
                "bidi_controls": bidi_controls,
                "tag_characters": tag_characters,
            },
        })

    parser = IntegrityHTMLParser()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        pass
    issues.extend(parser.hidden_issues)
    issues.extend(parser.link_issues)

    visible_text = re.sub(r"\s+", " ", " ".join(parser.all_text)).strip()
    for pattern in INSTRUCTION_PATTERNS:
        for match in pattern.finditer(visible_text):
            start, end = match.span()
            issues.append({
                "type": "instruction_text",
                "evidence": visible_text[max(0, start - 50):min(len(visible_text), end + 70)].strip(),
            })
    for match in INSTRUCTION_MARKERS.finditer(raw):
        start, end = match.span()
        issues.append({
            "type": "instruction_text",
            "evidence": raw[max(0, start - 50):min(len(raw), end + 70)].strip(),
        })
    return {"ok": not issues, "issues": issues}


def language_of(text: str) -> str:
    words = re.findall(r"[a-zäöüß]+", text.casefold())
    if not words:
        return "unknown"
    de = sum(word in GERMAN_STOPWORDS for word in words)
    en = sum(word in ENGLISH_STOPWORDS for word in words)
    if max(de, en) < 2 or de == en:
        return "unknown"
    return "de" if de > en else "en"


def _sentence_evidence(text: str, span: tuple[int, int]) -> str:
    start, end = span
    previous = list(EVIDENCE_BREAK.finditer(text, 0, start))
    sentence_start = previous[-1].end() if previous else 0
    following = EVIDENCE_BREAK.search(text, end)
    sentence_end = following.start() if following and following.group() == "•" else (
        following.end() if following else len(text)
    )
    evidence = text[sentence_start:sentence_end].strip(" \u2022")
    if len(evidence) > 240:
        evidence = text[start:end].strip()
    return evidence or text[start:end].strip()


def german_requirement(
    text: str,
    language: str,
    title: Optional[str] = None,
) -> tuple[str, list[str], bool]:
    title_text = str(title or "").strip()
    if title_text and any(pattern.search(title_text) for pattern in TITLE_GERMAN_REQUIRED):
        return "required", [title_text], False

    required_matches = [
        match.span()
        for pattern in REQUIRED_GERMAN
        for match in pattern.finditer(text)
    ]
    nice_matches = [
        match.span()
        for pattern in NICE_GERMAN
        for match in pattern.finditer(text)
    ]
    not_required_matches = [
        match.span()
        for pattern in NOT_REQUIRED_GERMAN
        for match in pattern.finditer(text)
    ]

    def is_qualified(span: tuple[int, int]) -> bool:
        end = span[1]
        clause_break = GERMAN_CLAUSE_BREAK.search(text, end)
        clause_end = clause_break.start() if clause_break else len(text)
        window_end = min(clause_end, end + 41)
        qualifier = GERMAN_QUALIFIER.search(text, end, window_end)
        return bool(qualifier and qualifier.start() - end <= 40)

    if required_matches:
        result = (
            "nice_to_have"
            if all(is_qualified(span) for span in required_matches)
            else "required"
        )
        matches = required_matches
    elif nice_matches:
        result = "nice_to_have"
        matches = nice_matches
    elif not_required_matches:
        result = "not_required"
        matches = not_required_matches
    else:
        matches = []
        result = "unknown"
    if matches:
        snippets = []
        for span in matches:
            snippet = _sentence_evidence(text, span)
            if snippet not in snippets:
                snippets.append(snippet)
        return result, snippets, False
    if language == "de":
        return "required", [], True
    return "unknown", [], False


def _agency_experience_mention(text: str, start: int) -> bool:
    prefix = text[max(0, start - 180):start]
    breaks = [prefix.rfind(char) for char in ".!?;\n•"]
    prefix = prefix[max(breaks) + 1:]
    has_experience = re.search(r"\b(?:experience|erfahrung|kenntnisse|skills)\b", prefix, re.I)
    has_relation = re.search(r"\b(?:in|mit|with)\b", prefix, re.I)
    return bool(has_experience and has_relation)


def parse_amount(value: str) -> Optional[float]:
    token = re.sub(r"(?i)\b(?:euro|eur)\b|€|\s", "", value).casefold()
    is_k = token.endswith("k")
    if is_k:
        token = token[:-1]
    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "," in token:
        tail = token.rsplit(",", 1)[1]
        token = token.replace(",", "") if len(tail) == 3 else token.replace(",", ".")
    elif "." in token:
        pieces = token.split(".")
        if len(pieces[-1]) == 3:
            token = "".join(pieces)
    try:
        number = float(token)
    except ValueError:
        return None
    return number * (1000 if is_k else 1)


def _salary_candidates(text: str) -> list[tuple[int, int, str, str, str]]:
    candidates = []
    occupied = []
    seen_ranges = set()
    for pattern in RANGE_PATTERNS:
        for match in pattern.finditer(text):
            left_span, right_span = match.span("left"), match.span("right")
            key = (left_span[0], right_span[1])
            if key in seen_ranges:
                continue
            seen_ranges.add(key)
            occupied.append(key)
            candidates.append((
                left_span[0], right_span[1],
                match.group("left"), match.group("right"), match.group(0),
            ))
    for match in SINGLE_RE.finditer(text):
        start, end = match.span()
        if any(range_start <= start and end <= range_end for range_start, range_end in occupied):
            continue
        candidates.append((start, end, match.group(0), match.group(0), match.group(0)))
    return sorted(candidates, key=lambda candidate: (candidate[0], candidate[1] - candidate[0]))


def _sentence_window(text: str, start: int, end: int, radius: int = 100) -> tuple[int, int]:
    window_start = max(0, start - radius)
    prefix = text[window_start:start]
    breaks = [prefix.rfind(char) for char in ".!?;\n"]
    last_break = max(breaks)
    if last_break >= 0:
        window_start += last_break + 1

    window_end = min(len(text), end + radius)
    suffix = text[end:window_end]
    boundary = SENTENCE_BREAK.search(suffix)
    if boundary:
        window_end = end + boundary.start()
    return window_start, window_end


def _nearest_context_distance(pattern: re.Pattern[str], text: str, start: int, end: int) -> Optional[int]:
    distances = []
    for match in pattern.finditer(text):
        if match.end() < start:
            distances.append(start - match.end())
        elif match.start() > end:
            distances.append(match.start() - end)
        else:
            distances.append(0)
    return min(distances) if distances else None


def _salary_details(text: str) -> tuple[Optional[dict[str, Any]], Optional[float]]:
    hours = list(WEEKLY_HOURS.finditer(text.casefold()))
    for start, end, left_text, right_text, raw in _salary_candidates(text):
        if any(match.start() <= start < match.end() for match in hours):
            continue
        left = parse_amount(left_text)
        right = parse_amount(right_text)
        if left is None or right is None:
            continue

        context_start, context_end = _sentence_window(text, start, end)
        context = text[context_start:context_end].casefold()
        local_start, local_end = start - context_start, end - context_start
        pay_distance = _nearest_context_distance(PAY_CONTEXT, context, local_start, local_end)
        non_salary_distance = _nearest_context_distance(NON_SALARY_ITEM, context, local_start, local_end)
        if (non_salary_distance is not None and non_salary_distance <= 28
                and (pay_distance is None or non_salary_distance < pay_distance)):
            continue

        minimum, maximum = sorted((left, right))
        has_pay_context = pay_distance is not None and pay_distance <= 100
        has_k_suffix = bool(re.search(r"\d\s*k\b", raw, re.I))
        has_currency = bool(re.search(r"€|\b(?:eur|euro)\b", raw, re.I))
        if not (has_pay_context or has_k_suffix or (has_currency and maximum >= 1000)):
            continue

        periods = [period for period, pattern in PAY_PERIODS.items() if pattern.search(context)]
        weekly_hours = None
        if "hour" in periods:
            period = "hour"
            if hours and not any(match["qualifier"] or match["range"] for match in hours):
                values = {float(match["hours"].replace(",", ".")) for match in hours}
                if len(values) == 1 and 0 < min(values) <= 168:
                    weekly_hours = values.pop()
            factor = 2080 if weekly_hours is None else weekly_hours * 52
            lower_bound, upper_bound = 8, 300
        elif "month" in periods:
            period = "month"
            factor = 12
            lower_bound, upper_bound = 400, 50000
        else:
            period = "year"
            factor = 1
            lower_bound, upper_bound = 6000, 1000000
        if minimum < lower_bound or maximum > upper_bound:
            continue
        salary = {
            "min": minimum,
            "max": maximum,
            "currency": "EUR",
            "period": period,
            "annual_min": minimum * factor,
            "annual_max": maximum * factor,
            "raw": raw.strip(),
        }
        annual_gross = None
        if (
            has_currency
            and len(periods) == 1
            and re.search(r"\b(?:gross|brutto)\b", context)
            and not UNCERTAIN_PAY_BASIS.search(context)
            and not (FULL_TIME.search(context) and PART_TIME.search(text))
            and (period != "hour" or weekly_hours is not None)
        ):
            annual_gross = salary["annual_min"]
        return salary, annual_gross
    return None, None


def salary_of(text: str) -> Optional[dict[str, Any]]:
    salary, _ = _salary_details(text)
    return salary


def remote_mode(text: str) -> str:
    lower = text.casefold()
    sentences = re.split(r"(?<=[.!?;])\s+|\n+", lower)
    access_pattern = re.compile(r"\b(?:remote|home[- ]?office|homeoffice|mobiles arbeiten|mobile work)\b")
    partial_pattern = re.compile(
        r"\b(?:bis zu\s+\d+\s*(?:tage?|days?)|up to\s+\d+\s+days?|"
        r"\d+\s*(?:tage?|days?)\s*(?:pro\s+woche|/\s*woche|per week|a week)|"
        r"teilweise|partially|partial|flexibel|flexible)\b"
    )
    for sentence in sentences:
        if access_pattern.search(sentence) and partial_pattern.search(sentence):
            return "hybrid"
        for match in re.finditer(r"\b(\d{1,3})\s*%\s*(?:remote|home[- ]?office|homeoffice)\b", sentence):
            if int(match.group(1)) < 100:
                return "hybrid"

    if re.search(r"\b(?:hybrid|hybrides arbeiten|hybrid work)\b", lower):
        return "hybrid"
    if re.search(
        r"\b(?:remote[- ]first|fully remote|completely remote|100\s*%\s*remote|"
        r"vollständig remote|vollstaendig remote|komplett remote|remote\s+in\s+"
        r"(?:germany|deutschland)|remote\s*\(\s*(?:germany|deutschland)\s*\))(?=\W|$)",
        lower,
    ):
        return "remote"
    if re.search(r"\b(?:home[- ]?office|homeoffice|mobiles arbeiten|mobile work)\b", lower):
        return "hybrid"
    if re.search(r"\b(?:vor ort|onsite|on-site|präsenz|praesenz)\b", lower):
        return "onsite"
    return "unknown"


def employment_type_from_title(title: Optional[str]) -> Optional[str]:
    lower = str(title or "").casefold()
    for employment_type, pattern in (
        ("werkstudent", r"\b(?:werkstudent(?:in)?|working[- ]student)\b"),
        ("praktikum", r"\b(?:intern(?:ship)?|praktik(?:um|ant|antin))\b"),
        ("trainee", r"\btrainee\b"),
        ("ausbildung", r"\b(?:ausbildung|apprenticeship|apprentice)\b"),
    ):
        if re.search(pattern, lower):
            return employment_type
    return None


def employment_types(text: str, title: Optional[str] = None) -> list[str]:
    title_employment = employment_type_from_title(title)
    if title_employment:
        return [title_employment]
    lower = text.casefold()
    patterns = (
        ("festanstellung/unbefristet", r"\b(?:festanstellung|unbefristet)\b"),
        ("befristet", r"\bbefristet\b"),
        ("vollzeit", r"\bvollzeit\b|\bfull[- ]time\b"),
        ("teilzeit", r"\bteilzeit\b|\bpart[- ]time\b"),
        ("werkstudent", r"\bwerkstudent(?:in)?\b"),
        ("praktikum", r"\bpraktik(?:um|ant|antin)\b|\binternship\b"),
        ("ausbildung", r"\bausbildung\b|\bapprenticeship\b"),
        ("freelance", r"\bfreelanc(?:e|er|ing)\b|\bselbstständig\b"),
        ("minijob", r"\bminijob\b|\bgeringfügig\b"),
        ("trainee", r"\btrainee\b"),
    )
    return [name for name, pattern in patterns if re.search(pattern, lower)]


def seniority_from_title(title: Optional[str]) -> Optional[str]:
    lower = str(title or "").casefold()
    for label, pattern in (
        ("senior", r"\b(?:senior|sr|staff|principal)\b"),
        ("lead", r"\blead\b"),
        ("junior", r"\b(?:junior|jr|entry[- ]level)\b"),
        ("intern", r"\b(?:intern(?:ship)?|praktik(?:um|ant|antin))\b"),
        ("student", r"\b(?:werkstudent(?:in)?|working[- ]student|student)\b"),
    ):
        if re.search(pattern, lower):
            return label
    return None


def seniority(text: str, title: Optional[str] = None) -> str:
    title_level = seniority_from_title(title)
    if title_level:
        return title_level
    lower = text.casefold()
    for label, pattern in (
        ("intern", r"\bintern(?:ship)?\b|\bpraktikum\b"),
        ("senior", r"\bsenior\b|\bstaff\b|\bprincipal\b"),
        ("manager", r"\bengineering manager\b|\bhead of\b|\bdirector\b|\bvice president\b|\bvp\b|\bteamleiter(?:in)?\b|\babteilungsleiter(?:in)?\b|\bleiter(?:in)?\b|\bleitung\b"),
        ("lead", r"\b(?:tech(?:nical)? lead|team lead|teamlead|lead developer|lead engineer)\b"),
        ("junior", r"\bjunior\b|\bentry[- ]level\b"),
        ("mid", r"\bmid[- ]level\b|\bintermediate\b"),
    ):
        if re.search(pattern, lower):
            return label
    return "unknown"


def benefits_of(text: str) -> dict[str, Any]:
    flags: dict[str, Any] = {}
    benefit_patterns = {
        "13th_salary_or_christmas_bonus": r"\b(?:13(?:th|\.|te[rn]?)\s*(?:salary|month|gehalt)|weihnachtsgeld|christmas bonus)\b",
        "holiday_pay": r"\burlaubsgeld\b",
        "bav": r"\bbav\b|betriebliche altersvorsorge",
        "vwl": r"\bvwl\b|vermögenswirksame leistungen",
        "collective_agreement": r"\btarifvertrag\b|\btv[- ]?l\b|\btvöd\b|\big metall\b",
        "jobrad": r"\bjobrad\b|business bike",
        "transit_ticket": r"\bdeutschlandticket\b|\bjobticket\b",
        "equity_or_vsop": r"\bvsop\b|\bequity\b|\bstock options?\b|unternehmensanteile",
    }
    lower = text.casefold()
    for name, pattern in benefit_patterns.items():
        flags[name] = bool(re.search(pattern, lower))
    vacation = re.search(r"\b(\d{1,2})\s*(?:tage?\s+urlaub|days?\s+(?:of\s+)?vacation|days?\s+annual leave)\b", lower)
    flags["vacation_days"] = int(vacation.group(1)) if vacation else None
    return flags


def analyze(
    text: str,
    title: Optional[str] = None,
    employer_name: Optional[str] = None,
    *,
    as_of: Optional[dt.date] = None,
) -> dict[str, Any]:
    integrity = integrity_of(text)
    text = clean_text(text)
    language = language_of(text)
    german, evidence, inferred = german_requirement(text, language, title)
    salary, annual_gross = _salary_details(text)
    reference_date = as_of or dt.date.today()
    amounts = official_amounts(reference_date)
    has_visa_amounts = all(key in amounts for key in VISA_KEYS)
    visa_check = None
    if annual_gross is not None and has_visa_amounts:
        visa_check = {key: annual_gross >= amounts[key] for key in VISA_KEYS}
    lower = text.casefold()
    agency_matches = []
    for label, value in (
        ("employer", employer_name),
        ("title", title),
        ("text", text),
    ):
        candidate = str(value or "")
        for pattern in AGENCY_PATTERNS:
            match = pattern.search(candidate)
            if match:
                if label == "text" and _agency_experience_mention(candidate, match.start()):
                    continue
                snippet = candidate[max(0, match.start() - 60):min(len(candidate), match.end() + 60)].strip()
                evidence_item = "{}: {}".format(label, snippet)
                if evidence_item not in agency_matches:
                    agency_matches.append(evidence_item)
    return {
        "integrity": integrity,
        "ad_language": language,
        "german_required": german,
        "german_evidence": evidence,
        "inferred": inferred,
        "english_required": bool(re.search(
            r"\b(?:fluent|business[- ]fluent|professional proficiency in)\s+english\b"
            r"|\benglish\s+(?:is\s+)?required\b"
            r"|\benglish is our\s+(?:working|company)\s+language\b"
            r"|\benglish is the (?:working|company) language\b"
            r"|\b(?:gute|sehr gute|fließende|fliessende|verhandlungssichere)\s+englischkenntnisse\b"
            r"|\benglischkenntnisse\s+(?:sind\s+)?(?:erforderlich|vorausgesetzt)\b"
            r"|\benglisch\s*\((?:b2|c1|c2)\)\b",
            lower,
        )),
        "salary": salary,
        "visa_salary_check": visa_check,
        "visa_salary_reference_year": reference_date.year if has_visa_amounts else None,
        "remote_mode": remote_mode(text),
        "employment_type": employment_types(text, title),
        "seniority": seniority(text, title),
        "agency_or_temp": bool(agency_matches),
        "agency_evidence": agency_matches,
        "benefits_signals": benefits_of(text),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze language, requirements and compensation signals in a job ad.")
    parser.add_argument("file", nargs="?", default="-", metavar="FILE|-",
                        help="Text/HTML file to analyze, or - for stdin (default)")
    args = parser.parse_args()
    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    write_output(json_text(analyze(text)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
