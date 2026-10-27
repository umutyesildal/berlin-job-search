from __future__ import annotations

import argparse
import json
import datetime as dt
from decimal import Decimal, InvalidOperation
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from _common import default_data_dir, escape_html, protect_data_dir, warn_data_dir_protection
from tracker import archive_folder, calculate_stats, events_path, followup_candidates, read_events, read_rows

REPORT_TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "application-report.html"


def escaped(value: Any) -> str:
    return escape_html(value or "", quote=True)


def score_text(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return ""
    try:
        number = Decimal(str(value))
        if number.is_finite():
            return format(number.quantize(Decimal("0.0")), ".1f")
    except (InvalidOperation, ValueError):
        pass
    return str(value)


def safe_posting_url(value: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme.casefold() in {"http", "https"} and bool(parsed.netloc)


def application_dates(
    rows: list[dict[str, str]],
    events: list[dict[str, Any]],
) -> dict[str, dt.date]:
    applied_events: dict[str, list[dt.date]] = {}
    for event in events:
        if event.get("type") != "applied":
            continue
        try:
            date = dt.date.fromisoformat(str(event.get("date") or ""))
        except ValueError:
            continue
        applied_events.setdefault(str(event.get("id") or ""), []).append(date)
    dates = {}
    for row in rows:
        identifier = str(row.get("id") or "")
        try:
            date = dt.date.fromisoformat(row.get("applied_date") or "")
        except ValueError:
            values = applied_events.get(identifier, [])
            if not values:
                continue
            date = min(values)
        dates[identifier] = date
    return dates


def svg_funnel(values: list[tuple[str, int]]) -> str:
    width, row_height, label_width, bar_width = 720, 42, 128, 450
    height = max(70, row_height * len(values) + 14)
    maximum = max((value for _, value in values), default=0)
    output = [
        '<svg class="chart" viewBox="0 0 {} {}" role="img" aria-label="Application funnel">'.format(width, height),
    ]
    for index, (label, value) in enumerate(values):
        y = 12 + index * row_height
        bar = int(bar_width * value / maximum) if maximum else 0
        output.extend([
            '<text x="0" y="{}" class="chart-label">{}</text>'.format(y + 21, escape_html(label)),
            '<rect data-stage="{}" data-value="{}" x="{}" y="{}" width="{}" height="24" rx="3" class="chart-bar"></rect>'.format(
                escape_html(label.casefold()), value, label_width, y, bar,
            ),
            '<text x="{}" y="{}" class="chart-value">{}</text>'.format(label_width + bar + 8, y + 18, value),
        ])
    output.append("</svg>")
    return "".join(output)


def svg_weekly(counts: list[tuple[str, int]]) -> str:
    width, height = 760, 220
    top, bottom = 14, 184
    max_count = max((count for _, count in counts), default=0)
    slot = (width - 42) / max(1, len(counts))
    bar_width = max(8, slot * 0.55)
    output = [
        '<svg class="chart weekly-chart" viewBox="0 0 {} {}" role="img" aria-label="Weekly applications">'.format(width, height),
        '<line x1="28" y1="{}" x2="{}" y2="{}" class="chart-axis"></line>'.format(bottom, width - 8, bottom),
    ]
    for index, (label, count) in enumerate(counts):
        x = 30 + index * slot + (slot - bar_width) / 2
        bar_height = int((bottom - top) * count / max_count) if max_count else 0
        y = bottom - bar_height
        output.append(
            '<rect x="{:.1f}" y="{}" width="{:.1f}" height="{}" rx="2" class="chart-bar">'
            '<title>{}: {}</title></rect>'.format(x, y, bar_width, bar_height, escape_html(label), count)
        )
        output.append(
            '<text x="{:.1f}" y="205" text-anchor="middle" class="chart-small">{}</text>'.format(
                x + bar_width / 2, escape_html(label),
            )
        )
        if count:
            output.append(
                '<text x="{:.1f}" y="{}" text-anchor="middle" class="chart-small">{}</text>'.format(
                    x + bar_width / 2, max(10, y - 4), count,
                )
            )
    output.append("</svg>")
    return "".join(output)


def weekly_applications(
    dates: dict[str, dt.date],
    today: Optional[dt.date] = None,
) -> list[tuple[str, int]]:
    today = today or dt.date.today()
    current_monday = today - dt.timedelta(days=today.weekday())
    starts = [current_monday - dt.timedelta(weeks=offset) for offset in reversed(range(12))]
    counts = {start: 0 for start in starts}
    for date in dates.values():
        monday = date - dt.timedelta(days=date.weekday())
        if monday in counts:
            counts[monday] += 1
    return [(start.strftime("%d %b"), counts[start]) for start in starts]


def action_rows(
    rows: list[dict[str, str]],
    *,
    today: Optional[dt.date] = None,
) -> list[dict[str, Any]]:
    today = today or dt.date.today()
    due, ghosted = followup_candidates(rows, today=today, days=10, maximum=2)
    result: dict[str, dict[str, Any]] = {}
    for row in due + ghosted:
        result[str(row.get("id") or "")] = dict(row)
    for row in rows:
        try:
            next_date = dt.date.fromisoformat(row.get("next_action_date") or "")
        except ValueError:
            continue
        if next_date <= today:
            item = result.setdefault(str(row.get("id") or ""), dict(row))
            if not item.get("suggested_action"):
                item["suggested_action"] = row.get("next_action") or "Next action due"
            item["days_silent"] = ""
    return list(result.values())


def posting_link(url: str) -> str:
    if not url or not safe_posting_url(url):
        return escaped(url or "—")
    return '<a href="{}" rel="noreferrer">{}</a>'.format(escaped(url), escaped(url))


def archive_links(root: Path, row: dict[str, str], report_directory: Path) -> str:
    folder = archive_folder(root, row)
    if not folder.is_dir():
        return "—"
    links = []
    for filename, label in (("outcome.md", "Outcome"), ("posting.md", "Posting archive")):
        target = folder / filename
        if target.is_file():
            relative = os.path.relpath(target, report_directory).replace(os.sep, "/")
            href = urllib.parse.quote(relative, safe="/.-_")
            links.append('<a href="{}">{}</a>'.format(escaped(href), label))
    return " · ".join(links) if links else "—"


def applications_table(
    rows: list[dict[str, str]],
    root: Path,
    report_directory: Path,
) -> str:
    columns = [
        ("company", "Company"),
        ("title", "Role"),
        ("status", "Status"),
        ("score", "Score"),
        ("applied_date", "Applied"),
        ("last_event_date", "Last event"),
    ]
    output = ['<div class="table-tools"><label for="application-filter">Filter applications</label> '
              '<input id="application-filter" type="search" placeholder="Company, role, status…"></div>']
    output.append('<div class="table-wrap"><table id="applications"><thead><tr>')
    for key, label in columns:
        output.append(
            '<th data-sort="{}" tabindex="0" role="button" aria-label="Sort by {}">{}</th>'.format(
                escaped(key), escaped(label), escaped(label),
            )
        )
    output.append("<th>Links</th><th>Archive</th></tr></thead><tbody>")
    for row in rows:
        output.append("<tr>")
        for key, _ in columns:
            value = score_text(row.get(key)) if key == "score" else (row.get(key) or "")
            output.append('<td data-sort="{}">{}</td>'.format(escaped(value.casefold()), escaped(value)))
        output.append(
            "<td>{}</td><td>{}</td>".format(
                posting_link(row.get("url") or ""),
                archive_links(root, row, report_directory),
            )
        )
        output.append("</tr>")
    if not rows:
        output.append('<tr class="empty-row"><td colspan="8">No applications tracked yet.</td></tr>')
    output.append("</tbody></table></div>")
    return "".join(output)


def build_report(
    rows: list[dict[str, str]],
    events: list[dict[str, Any]],
    root: Path,
    report_path: Path,
    *,
    today: Optional[dt.date] = None,
) -> str:
    today = today or dt.date.today()
    stats = calculate_stats(rows, events)
    applied_dates = application_dates(rows, events)
    funnel = svg_funnel([
        ("Tracked", stats["tracked"]),
        ("Applied", stats["applied"]),
        ("Responded", stats["responded"]),
        ("Interview", stats["interviews"]),
        ("Offer", stats["offers"]),
    ])

    status_rows = [
        '<tr><td>{}</td><td>{}</td></tr>'.format(escaped(status), count)
        for status, count in stats["by_status"].items()
    ]
    if not rows:
        status_rows = ['<tr><td colspan="2">No applications tracked yet.</td></tr>']
    source_rows = []
    for source, values in stats["by_source"].items():
        source_rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}%</td></tr>".format(
                escaped(source), values["tracked"], values["applied"], values["response_rate_pct"],
            )
        )
    if not source_rows:
        source_rows = ['<tr><td colspan="4">No source data yet.</td></tr>']

    actions = action_rows(rows, today=today)
    action_html = []
    for row in actions:
        action_html.append(
            "<li><strong>{}</strong> — {} — {} ({})</li>".format(
                escaped(row.get("company") or "Unknown company"),
                escaped(row.get("title") or "Untitled role"),
                escaped(row.get("suggested_action") or "Review"),
                escaped("{} days silent".format(row.get("days_silent")) if row.get("days_silent") != "" else "next action due"),
            )
        )
    if not action_html:
        action_html = ["<li>Nothing due right now.</li>"]

    cards = [
        ("Tracked", stats["tracked"]),
        ("Applied", stats["applied"]),
        ("Response rate", "{}%".format(stats["response_rate_pct"])),
        ("Interviews", stats["interviews"]),
        ("Offers", stats["offers"]),
        ("Follow-ups due", stats["followups_due"]),
    ]
    card_html = "".join(
        '<article class="stat-card"><span>{}</span><strong>{}</strong></article>'.format(
            escaped(label), value,
        )
        for label, value in cards
    )
    generated = dt.datetime.now(dt.timezone.utc).isoformat()
    table = applications_table(rows, root, report_path.parent)
    weekly = svg_weekly(weekly_applications(applied_dates, today=today))
    template = REPORT_TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "__GENERATED__": escaped(generated),
        "__CARD_HTML__": card_html,
        "__FUNNEL__": funnel,
        "__STATUS_ROWS__": "".join(status_rows),
        "__SOURCE_ROWS__": "".join(source_rows),
        "__WEEKLY__": weekly,
        "__ACTIONS__": "".join(action_html),
        "__TABLE__": table,
    }
    token = re.compile("|".join(re.escape(marker) for marker in replacements))
    return token.sub(lambda match: replacements[match.group(0)], template)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build a self-contained offline application dashboard.")
    parser.add_argument("--data-dir", help="Tracker data directory (default: GJS_DATA_DIR or ./job-search)")
    parser.add_argument("--out", help="HTML report path (default: DATA_DIR/report.html)")
    args = parser.parse_args(argv)
    root = Path(args.data_dir).expanduser().resolve() if args.data_dir else default_data_dir().expanduser().resolve()
    output_path = Path(args.out) if args.out else root / "report.html"
    try:
        warn_data_dir_protection(protect_data_dir(root))
        rows = read_rows(root / "tracker.csv")
        events = read_events(events_path(root))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            build_report(rows, events, root, output_path),
            encoding="utf-8",
        )
        print("Report: {}".format(output_path))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
