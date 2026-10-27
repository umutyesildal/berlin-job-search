from __future__ import annotations

import argparse
import csv
import datetime as dt
from decimal import Decimal, InvalidOperation
import json
import re
import shutil
import statistics
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from _common import (
    canonical_url,
    csv_text,
    default_data_dir,
    json_text,
    markdown_table,
    parse_date_or_none,
    protect_data_dir,
    protect_if_inside_data_dir,
    slugify,
    spreadsheet_cell,
    warn_data_dir_protection,
)

FIELDS = [
    "id", "date_added", "company", "title", "url", "location", "source", "status",
    "score", "next_action", "next_action_date", "notes", "applied_date",
    "last_event_date", "followup_count",
]
STATUSES = {
    "saved", "applying", "applied", "interview", "offer", "accepted",
    "rejected", "withdrawn", "ghosted",
}
EVENT_TYPES = {
    "applied", "screen", "interview", "assessment", "offer", "accepted",
    "rejected", "withdrawn", "ghosted", "followup_sent", "thank_you_sent", "note",
}
EVENT_STATUSES = {
    "applied": "applied",
    "screen": "interview",
    "interview": "interview",
    "assessment": "interview",
    "offer": "offer",
    "accepted": "accepted",
    "rejected": "rejected",
    "withdrawn": "withdrawn",
    "ghosted": "ghosted",
}
RESPONSE_EVENTS = {"screen", "interview", "assessment", "offer", "rejected"}
INTERVIEW_EVENTS = {"screen", "interview", "assessment"}
OFFER_EVENTS = {"offer", "accepted"}
APPLIED_STATUSES = {"applied", "interview", "offer", "accepted", "rejected"}
RESPONDED_STATUSES = {"interview", "offer", "accepted", "rejected"}
INTERVIEW_STATUSES = {"interview", "offer", "accepted"}
OFFER_STATUSES = {"offer", "accepted"}


def data_dir() -> Path:
    return default_data_dir()


def _protect_tracker_data() -> dict[str, Any]:
    result = protect_data_dir(data_dir())
    warn_data_dir_protection(result)
    return result


def _protect_write_target(target: Path) -> None:
    result = protect_if_inside_data_dir(target)
    if result is not None:
        warn_data_dir_protection(result)


def tracker_path() -> Path:
    return data_dir() / "tracker.csv"


def events_path(root: Optional[Path] = None) -> Path:
    return (root or data_dir()) / "events.jsonl"


def normalized_row(row: dict[str, Any]) -> dict[str, str]:
    normalized = {field: str(row.get(field) or "") for field in FIELDS}
    if not normalized["followup_count"]:
        normalized["followup_count"] = "0"
    return normalized


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        rows = []
        for row in csv.DictReader(stream):
            for key, value in row.items():
                # Decode only the exact escape emitted by spreadsheet_cell;
                # historical ordinary apostrophes remain untouched.
                if row.get("_csv_encoding") == "spreadsheet-v1" and isinstance(value, str) and value.startswith("'"):
                    if spreadsheet_cell(value[1:]) == value:
                        row[key] = value[1:]
            rows.append(normalized_row(row))
        return rows


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    _protect_write_target(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = csv_text([{**normalized_row(row), "_csv_encoding": "spreadsheet-v1"} for row in rows], FIELDS + ["_csv_encoding"])
    with path.open("w", newline="", encoding="utf-8") as stream:
        stream.write(text)


def normalize_score(value: Any) -> str:
    if value is None or not str(value).strip():
        return ""
    try:
        score = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError("--score must be a number from 0.0 to 5.0 with at most one decimal place")
    if not score.is_finite() or score < 0 or score > 5 or score.as_tuple().exponent < -1:
        raise ValueError("--score must be a number from 0.0 to 5.0 with at most one decimal place")
    if score == 0:
        score = Decimal("0")
    return format(score.quantize(Decimal("0.0")), ".1f")


def add_job(path: Path, job: dict[str, Any], score: str = "", notes: str = "") -> str:
    normalized_score = normalize_score(score)
    rows = read_rows(path)
    key = canonical_url(str(job.get("url") or ""))
    if key and any(canonical_url(row.get("url", "")) == key for row in rows):
        raise ValueError("A job with this canonical URL is already tracked.")
    identifier = uuid.uuid4().hex[:10]
    row = {
        "id": identifier,
        "date_added": dt.date.today().isoformat(),
        "company": job.get("company", ""),
        "title": job.get("title", ""),
        "url": job.get("url", ""),
        "location": job.get("location", ""),
        "source": job.get("source", ""),
        "status": "saved",
        "score": normalized_score,
        "next_action": "",
        "next_action_date": "",
        "notes": notes,
        "applied_date": "",
        "last_event_date": "",
        "followup_count": "0",
    }
    rows.append(row)
    write_rows(path, rows)
    return identifier


def validate_date(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must use YYYY-MM-DD")
    if parse_date_or_none(value) is None:
        raise ValueError("invalid date '{}'".format(value))
    return value


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError("invalid events.jsonl line {}".format(line_number)) from error
            if isinstance(event, dict):
                events.append(event)
    return events


def archive_folder(root: Path, row: dict[str, Any]) -> Path:
    identifier = re.sub(r"[^A-Za-z0-9_-]+", "-", str(row.get("id") or "unknown")).strip("-") or "unknown"
    return root / "applications" / "{}-{}-{}".format(
        identifier,
        slugify(str(row.get("company") or "")),
        slugify(str(row.get("title") or "")),
    )


def timeline_line(event: dict[str, Any]) -> str:
    note = str(event.get("note") or "").replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    suffix = " — {}".format(note) if note else ""
    return "- {} — {}{}".format(event.get("date") or "", event.get("type") or "note", suffix)


def outcome_header(row: dict[str, Any]) -> str:
    fields = (
        ("Company", row.get("company")),
        ("Role", row.get("title")),
        ("URL", row.get("url")),
        ("Score", row.get("score")),
        ("Applied date", row.get("applied_date")),
    )
    lines = ["# Application outcome", ""]
    lines.extend("- **{}:** {}".format(label, value or "—") for label, value in fields)
    lines.extend(["", "## Timeline", ""])
    return "\n".join(lines)


def write_outcome(path: Path, row: dict[str, Any], events: list[dict[str, Any]]) -> None:
    _protect_write_target(path)
    relevant = [event for event in events if str(event.get("id")) == str(row.get("id"))]
    relevant.sort(key=lambda event: (str(event.get("date") or ""), str(event.get("recorded_at") or "")))
    content = outcome_header(row)
    if relevant:
        content += "\n".join(timeline_line(event) for event in relevant) + "\n"
    path.write_text(content, encoding="utf-8")


def append_outcome_event(path: Path, row: dict[str, Any], event: dict[str, Any]) -> None:
    _protect_write_target(path)
    if not path.exists():
        write_outcome(path, row, [event])
        return
    content = path.read_text(encoding="utf-8")
    addition = ""
    if "## Timeline" not in content:
        addition = "\n\n## Timeline\n\n"
    elif not content.endswith("\n"):
        addition = "\n"
    with path.open("a", encoding="utf-8") as stream:
        if addition:
            stream.write(addition)
        stream.write(timeline_line(event) + "\n")


def record_event(
    path: Path,
    identifier: str,
    event_type: str,
    *,
    event_date: Optional[str] = None,
    note: str = "",
) -> dict[str, Any]:
    if event_type not in EVENT_TYPES:
        raise ValueError("unknown event type '{}'; use {}".format(event_type, ", ".join(sorted(EVENT_TYPES))))
    date_value = validate_date(event_date or dt.date.today().isoformat())
    rows = read_rows(path)
    row = next((item for item in rows if item.get("id") == identifier), None)
    if row is None:
        raise ValueError("No tracked job with id '{}'.".format(identifier))

    event = {
        "id": identifier,
        "date": date_value,
        "type": event_type,
        "note": note or "",
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if event_type in EVENT_STATUSES:
        row["status"] = EVENT_STATUSES[event_type]
    if event_type == "applied" and not row.get("applied_date"):
        row["applied_date"] = date_value
    if event_type == "followup_sent":
        try:
            row["followup_count"] = str(int(row.get("followup_count") or "0") + 1)
        except ValueError:
            row["followup_count"] = "1"
    row["last_event_date"] = date_value
    _protect_write_target(path)
    write_rows(path, rows)

    event_file = events_path(path.parent)
    _protect_write_target(event_file)
    event_file.parent.mkdir(parents=True, exist_ok=True)
    with event_file.open("a", encoding="utf-8") as stream:
        stream.write(json_text(event, indent=None) + "\n")

    folder = archive_folder(path.parent, row)
    if folder.is_dir():
        outcome = folder / "outcome.md"
        if outcome.exists():
            append_outcome_event(outcome, row, event)
        else:
            write_outcome(outcome, row, read_events(event_file))
    return event


def followup_candidates(
    rows: list[dict[str, str]],
    *,
    today: Optional[dt.date] = None,
    days: int = 10,
    maximum: int = 2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    today = today or dt.date.today()
    due, ghosted = [], []
    for row in rows:
        if row.get("status") not in {"applied", "interview"}:
            continue
        last_event = parse_date_or_none(row.get("last_event_date")) or parse_date_or_none(row.get("applied_date"))
        if last_event is None:
            continue
        silent_days = (today - last_event).days
        try:
            count = int(row.get("followup_count") or "0")
        except ValueError:
            count = 0
        result = dict(row)
        result["days_silent"] = silent_days
        if silent_days < 0:
            continue
        if count < maximum and silent_days >= days:
            result["suggested_action"] = "follow-up #{}".format(count + 1)
            due.append(result)
        elif count >= maximum and silent_days >= 21:
            result["suggested_action"] = "mark ghosted"
            ghosted.append(result)
    return due, ghosted


def application_stage_flags(
    row: dict[str, Any],
    row_events: list[dict[str, Any]],
) -> dict[str, Any]:
    status = str(row.get("status") or "").casefold()
    applied_events = [event for event in row_events if event.get("type") == "applied"]
    applied_date = parse_date_or_none(row.get("applied_date"))
    if applied_date is None:
        applied_dates = [
            date for date in (parse_date_or_none(event.get("date")) for event in applied_events)
            if date is not None
        ]
        if applied_dates:
            applied_date = min(applied_dates)
    applied_record = min(
        (
            event for event in applied_events
            if parse_date_or_none(event.get("date")) == applied_date
        ),
        key=lambda event: str(event.get("recorded_at") or ""),
        default=None,
    )

    def later_than_applied(event: dict[str, Any]) -> bool:
        event_date = parse_date_or_none(event.get("date"))
        if event_date is None or (applied_date is not None and event_date < applied_date):
            return False
        if applied_date is None or event_date > applied_date or applied_record is None:
            return True
        recorded_at = str(event.get("recorded_at") or "")
        applied_at = str(applied_record.get("recorded_at") or "")
        return not recorded_at or not applied_at or recorded_at > applied_at

    applied = bool(applied_events or applied_date is not None or status in APPLIED_STATUSES)
    responses = [
        event for event in row_events
        if event.get("type") in RESPONSE_EVENTS and later_than_applied(event)
    ]
    interviews = [
        event for event in row_events
        if event.get("type") in INTERVIEW_EVENTS and later_than_applied(event)
    ]
    offers = [
        event for event in row_events
        if event.get("type") in OFFER_EVENTS and later_than_applied(event)
    ]
    responded = applied and (bool(responses) or status in RESPONDED_STATUSES)
    offer = applied and (bool(offers) or status in OFFER_STATUSES)
    interview = applied and (
        bool(interviews) or status in INTERVIEW_STATUSES or offer
    )
    return {
        "applied": applied,
        "responded": responded or interview or offer,
        "interview": interview or offer,
        "offer": offer,
        "applied_date": applied_date,
        "response_events": responses,
    }


def application_stage_counts(
    rows: list[dict[str, str]],
    events: list[dict[str, Any]],
) -> dict[str, int]:
    events_by_id: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        events_by_id.setdefault(str(event.get("id") or ""), []).append(event)
    counts = {"tracked": len(rows), "applied": 0, "responded": 0, "interview": 0, "offer": 0}
    for row in rows:
        flags = application_stage_flags(row, events_by_id.get(str(row.get("id") or ""), []))
        for stage in ("applied", "responded", "interview", "offer"):
            counts[stage] += int(flags[stage])
    return counts


def calculate_stats(rows: list[dict[str, str]], events: list[dict[str, Any]]) -> dict[str, Any]:
    by_status = {
        status: count
        for status in sorted(STATUSES)
        if (count := sum(row.get("status") == status for row in rows)) > 0
    }
    source_names = sorted({row.get("source") or "(unspecified)" for row in rows})
    by_source: dict[str, dict[str, Any]] = {
        source: {"tracked": 0, "applied": 0, "responded": 0, "response_rate_pct": 0}
        for source in source_names
    }
    events_by_id: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        events_by_id.setdefault(str(event.get("id") or ""), []).append(event)

    counts = {"tracked": len(rows), "applied": 0, "responded": 0, "interview": 0, "offer": 0}
    response_days = []
    for row in rows:
        source = row.get("source") or "(unspecified)"
        by_source[source]["tracked"] += 1
        flags = application_stage_flags(row, events_by_id.get(str(row.get("id") or ""), []))
        for stage in ("applied", "responded", "interview", "offer"):
            counts[stage] += int(flags[stage])
        by_source[source]["applied"] += int(flags["applied"])
        by_source[source]["responded"] += int(flags["responded"])
        if not flags["applied"]:
            continue
        applied_date = flags["applied_date"]
        if applied_date is None:
            continue
        responses = flags["response_events"]
        response_dates = [
            date for date in (parse_date_or_none(event.get("date")) for event in responses)
            if date is not None
        ]
        if response_dates:
            response_days.append((min(response_dates) - applied_date).days)
        elif flags["responded"]:
            last_event = parse_date_or_none(row.get("last_event_date"))
            if last_event is not None and last_event >= applied_date:
                response_days.append((last_event - applied_date).days)

    for source, source_stats in by_source.items():
        source_stats["response_rate_pct"] = (
            round(100 * source_stats["responded"] / source_stats["applied"])
            if source_stats["applied"] else 0
        )
    due_followups, ghosted_followups = followup_candidates(rows)
    return {
        "tracked": counts["tracked"],
        "by_status": by_status,
        "by_source": by_source,
        "applied": counts["applied"],
        "responded": counts["responded"],
        "response_rate_pct": round(100 * counts["responded"] / counts["applied"]) if counts["applied"] else 0,
        "interviews": counts["interview"],
        "interview_rate_pct": round(100 * counts["interview"] / counts["applied"]) if counts["applied"] else 0,
        "offers": counts["offer"],
        "offer_rate_pct": round(100 * counts["offer"] / counts["applied"]) if counts["applied"] else 0,
        "median_days_to_first_response": statistics.median(response_days) if response_days else None,
        "followups_due": len(due_followups) + len(ghosted_followups),
    }


def print_table(rows: list[dict[str, Any]], columns: Optional[list[str]] = None) -> None:
    columns = columns or [
        "id", "company", "title", "location", "status", "score", "next_action", "next_action_date",
    ]
    print(markdown_table(columns, [[row.get(key) for key in columns] for row in rows], spaced_separator=True))


def command_add(args: argparse.Namespace) -> int:
    if args.from_json:
        payload = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        jobs = payload.get("jobs", [])
        if args.index < 0 or args.index >= len(jobs):
            raise ValueError("Job index {} is out of range ({} jobs).".format(args.index, len(jobs)))
        job = jobs[args.index]
    else:
        job = {
            "company": args.company, "title": args.title, "url": args.url,
            "location": args.location, "source": args.source,
        }
    _protect_tracker_data()
    identifier = add_job(tracker_path(), job, args.score or "", args.notes or "")
    print("Added {} ({})".format(job.get("title", "job"), identifier))
    return 0


def command_status(args: argparse.Namespace) -> int:
    if args.new_status not in STATUSES:
        raise ValueError("Unknown status '{}'; use {}".format(args.new_status, ", ".join(sorted(STATUSES))))
    path = tracker_path()
    rows = read_rows(path)
    row = next((item for item in rows if item.get("id") == args.id), None)
    if row is None:
        raise ValueError("No tracked job with id '{}'.".format(args.id))
    row["status"] = args.new_status
    if args.new_status == "applied" and not row.get("applied_date"):
        row["applied_date"] = dt.date.today().isoformat()
    if args.note:
        row["notes"] = "{}{}".format(row.get("notes", ""), ("\n" if row.get("notes") else "") + args.note)
    _protect_tracker_data()
    write_rows(path, rows)
    record_event(path, args.id, "note", note="status → {}".format(args.new_status))
    print("Updated {}".format(args.id))
    return 0


def command_event(args: argparse.Namespace) -> int:
    event = record_event(
        tracker_path(),
        args.id,
        args.type,
        event_date=args.date,
        note=args.note or "",
    )
    print("Recorded {} for {}".format(event["type"], args.id))
    return 0


def copy_into_archive(source_name: Optional[str], folder: Path) -> None:
    if not source_name:
        return
    source = Path(source_name)
    if not source.is_file():
        raise ValueError("archive input file not found: {}".format(source))
    destination = folder / source.name
    if destination.exists():
        if destination.read_bytes() == source.read_bytes():
            return
        print("warning: preserving existing archive file {}".format(destination.name), file=sys.stderr)
        return
    _protect_write_target(destination)
    shutil.copy2(source, destination)


def fetch_posting(url: str) -> dict[str, Any]:
    from job_detail import fetch_detail

    return fetch_detail(url)


def write_fetched_posting(folder: Path, row: dict[str, Any]) -> None:
    url = str(row.get("url") or "")
    if not url:
        print("warning: cannot fetch posting without a URL", file=sys.stderr)
        return
    try:
        detail = fetch_posting(url)
    except Exception as error:
        print("warning: posting fetch failed: {}".format(error), file=sys.stderr)
        return
    payload = dict(detail)
    payload["fetched_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    json_path = folder / "posting.json"
    markdown_path = folder / "posting.md"
    for path, content in (
        (json_path, json_text(payload) + "\n"),
        (markdown_path, "\n".join([
            "# {}".format(detail.get("title") or row.get("title") or "Job posting"),
            "",
            "- Company: {}".format(detail.get("company") or row.get("company") or "—"),
            "- Location: {}".format(detail.get("location") or row.get("location") or "—"),
            "- URL: {}".format(detail.get("url") or url),
            "- Fetched at: {}".format(payload["fetched_at"]),
            "",
            "## Description",
            "",
            detail.get("description_text") or "No readable description available.",
            "",
        ])),
    ):
        if path.exists():
            print("warning: preserving existing archive file {}".format(path.name), file=sys.stderr)
        else:
            _protect_write_target(path)
            path.write_text(content, encoding="utf-8")


def command_archive(args: argparse.Namespace) -> int:
    path = tracker_path()
    rows = read_rows(path)
    row = next((item for item in rows if item.get("id") == args.id), None)
    if row is None:
        raise ValueError("No tracked job with id '{}'.".format(args.id))
    folder = archive_folder(path.parent, row)
    _protect_tracker_data()
    _protect_write_target(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for source in (args.cv, args.letter, args.posting):
        copy_into_archive(source, folder)
    if args.fetch_posting:
        write_fetched_posting(folder, row)
    outcome = folder / "outcome.md"
    if not outcome.exists():
        write_outcome(outcome, row, read_events(events_path(path.parent)))
    print("Archived {} at {}".format(args.id, folder))
    return 0


def command_followups(args: argparse.Namespace) -> int:
    if args.days < 0 or args.maximum < 0:
        raise ValueError("--days and --max must be non-negative")
    due, ghosted = followup_candidates(
        read_rows(tracker_path()),
        days=args.days,
        maximum=args.maximum,
    )
    columns = ["id", "company", "title", "status", "days_silent", "suggested_action"]
    print("Follow-ups due")
    print_table(due, columns)
    print("\nMark ghosted ({}+ follow-ups, 21+ days silent)".format(args.maximum))
    print_table(ghosted, columns)
    return 0


def format_stats_markdown(stats: dict[str, Any]) -> str:
    lines = [
        "# Application tracker statistics",
        "",
        "- Tracked: {}".format(stats["tracked"]),
        "- Applied: {}".format(stats["applied"]),
        "- Response rate: {}%".format(stats["response_rate_pct"]),
        "- Interview rate: {}%".format(stats["interview_rate_pct"]),
        "- Offer rate: {}%".format(stats["offer_rate_pct"]),
        "- Median days to first response: {}".format(
            stats["median_days_to_first_response"]
            if stats["median_days_to_first_response"] is not None else "—"
        ),
        "- Follow-ups due: {}".format(stats["followups_due"]),
        "",
        "## By status",
        "",
    ]
    lines.append(markdown_table(
        ["Status", "Count"],
        [[status, count] for status, count in stats["by_status"].items()],
        separators=["---", "---:"],
    ))
    lines.extend(["", "## By source", ""])
    lines.append(markdown_table(
        ["Source", "Tracked", "Applied", "Responded", "Response rate"],
        [[source, values["tracked"], values["applied"], values["responded"], "{}%".format(values["response_rate_pct"])]
         for source, values in stats["by_source"].items()],
        separators=["---", "---:", "---:", "---:", "---:"],
    ))
    return "\n".join(lines)


def command_stats(args: argparse.Namespace) -> int:
    stats = calculate_stats(read_rows(tracker_path()), read_events(events_path()))
    output = json_text(stats) if args.format == "json" else format_stats_markdown(stats)
    print(output)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Maintain a local CSV application tracker.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add = subparsers.add_parser("add", help="Add a job (duplicate canonical URLs are rejected)")
    add.add_argument("--company")
    add.add_argument("--title")
    add.add_argument("--url")
    add.add_argument("--location")
    add.add_argument("--source")
    add.add_argument("--score", help="Quick score from 0.0 to 5.0 (stored to one decimal place)")
    add.add_argument("--notes")
    add.add_argument("--from-json", help="Search JSON output to import from")
    add.add_argument("--index", type=int, default=0, help="Zero-based index in the jobs array")
    listing = subparsers.add_parser("list", help="List tracked jobs")
    listing.add_argument("--status", metavar="S")
    status = subparsers.add_parser("status", help="Update a job status")
    status.add_argument("id", metavar="ID")
    status.add_argument("new_status", metavar="NEW_STATUS",
                        help="One of: {}".format(", ".join(sorted(STATUSES))))
    status.add_argument("--note", help="Append a note to the tracked row")
    event = subparsers.add_parser("event", help="Record an application event")
    event.add_argument("id", metavar="ID")
    event.add_argument("type", choices=sorted(EVENT_TYPES), metavar="TYPE",
                       help="One of: {}".format(", ".join(sorted(EVENT_TYPES))))
    event.add_argument("--date", help="Event date (YYYY-MM-DD; default: today)")
    event.add_argument("--note", help="Optional event note")
    archive = subparsers.add_parser("archive", help="Archive application documents and posting details")
    archive.add_argument("id", metavar="ID")
    archive.add_argument("--cv")
    archive.add_argument("--letter")
    archive.add_argument("--posting")
    archive.add_argument("--fetch-posting", action="store_true",
                         help="Fetch the public posting from its tracked URL")
    followups = subparsers.add_parser("followups", help="Find overdue follow-ups")
    followups.add_argument("--days", type=int, default=10, help="Silence threshold in days (default: 10)")
    followups.add_argument("--max", dest="maximum", type=int, default=2,
                           help="Maximum follow-ups before suggesting ghosted (default: 2)")
    stats = subparsers.add_parser("stats", help="Summarize applications and outcomes")
    stats.add_argument("--format", choices=("md", "json"), default="md")
    subparsers.add_parser("protect", help="Protect the local job-search data directory")
    subparsers.add_parser("due", help="List rows with next_action_date due today or earlier")
    args = parser.parse_args(argv)
    try:
        if args.command == "protect":
            result = _protect_tracker_data()
            print(json_text(result))
            return 0
        path = tracker_path()
        if args.command == "add":
            if args.from_json:
                return command_add(args)
            if not args.company or not args.title or not args.url:
                parser.error("add requires --company, --title, and --url, or --from-json")
            return command_add(args)
        if args.command == "status":
            return command_status(args)
        if args.command == "event":
            return command_event(args)
        if args.command == "archive":
            return command_archive(args)
        if args.command == "followups":
            return command_followups(args)
        if args.command == "stats":
            return command_stats(args)
        rows = read_rows(path)
        if args.command == "list":
            selected = [row for row in rows if not args.status or row.get("status") == args.status]
        else:
            today = dt.date.today().isoformat()
            selected = [row for row in rows if row.get("next_action_date") and row["next_action_date"] <= today]
        print_table(selected)
        return 0
    except (ValueError, OSError, json.JSONDecodeError, csv.Error) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
