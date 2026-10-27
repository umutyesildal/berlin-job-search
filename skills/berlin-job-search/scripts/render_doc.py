from __future__ import annotations

import argparse
import base64
import html
import mimetypes
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from _common import protect_if_inside_data_dir, warn_data_dir_protection


INLINE_TOKEN = re.compile(
    r"\[([^\]]+)\]\(([^)]+)\)|\*\*(.+?)\*\*|\*([^*]+)\*|(<br\s*/?>)",
    re.I,
)
HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
LIST_ITEM = re.compile(r"^(\s*)[-*]\s+(.+?)\s*$")
DATE_LINE = re.compile(
    r"(?:\d{1,2}[./]\d{4}|\d{4})\s*[–—-]\s*"
    r"(?:\d{1,2}[./]\d{4}|\d{4}|present|today|now|heute)\s*[·•]\s*.+",
    re.I,
)
TABLE_SEPARATOR_CELL = re.compile(r":?-{3,}:?")


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        return {}, text
    metadata: dict[str, Any] = {}
    recipients: list[str] = []
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip().casefold()
        value = value.strip().replace("\\n", "\n")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key == "recipient":
            recipients.extend(part.strip() for part in value.splitlines() if part.strip())
        else:
            metadata[key] = value
    if recipients:
        metadata["recipient"] = recipients
    return metadata, "\n".join(lines[end + 1:])


def safe_url(value: str) -> bool:
    if not value or any(ord(char) < 32 for char in value):
        return False
    try:
        parsed = urllib.parse.urlsplit(value.strip())
    except ValueError:
        return False
    scheme = parsed.scheme.casefold()
    if scheme in {"http", "https"}:
        return bool(parsed.netloc) and not parsed.username and not parsed.password
    return scheme == "mailto" and bool(parsed.path) and not parsed.netloc


def render_inline(text: str, *, table_cell: bool = False) -> str:
    output: list[str] = []
    position = 0
    for match in INLINE_TOKEN.finditer(text):
        output.append(html.escape(text[position:match.start()]))
        if match.group(1) is not None:
            source = match.group(0)
            address = match.group(2).strip()
            if safe_url(address):
                output.append(
                    '<a href="{}">{}</a>'.format(
                        html.escape(address, quote=True),
                        render_inline(match.group(1)),
                    )
                )
            else:
                output.append(html.escape(source))
        elif match.group(3) is not None:
            output.append("<strong>{}</strong>".format(render_inline(match.group(3))))
        elif match.group(4) is not None:
            output.append("<em>{}</em>".format(render_inline(match.group(4))))
        elif table_cell:
            output.append("<br>")
        else:
            output.append(html.escape(match.group(5)))
        position = match.end()
    output.append(html.escape(text[position:]))
    return "".join(output)


def split_table_row(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    protected = value.replace(r"\|", "\x00")
    return [cell.replace("\x00", "|").strip() for cell in protected.split("|")]


def is_table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines) or not lines[index].strip().startswith("|"):
        return False
    cells = split_table_row(lines[index + 1])
    return bool(cells) and all(TABLE_SEPARATOR_CELL.fullmatch(cell.replace(" ", "")) for cell in cells)


def render_table(lines: list[str], style: str) -> str:
    rows = [split_table_row(line) for line in lines if line.strip().startswith("|")]
    if len(rows) < 2:
        return ""
    headers = rows[0]
    body = rows[2:]
    if style == "lebenslauf":
        output = ['<table class="cv-table">', "<thead><tr>"]
        output.extend("<th>{}</th>".format(render_inline(cell, table_cell=True)) for cell in headers)
        output.append("</tr></thead><tbody>")
        for row in body:
            output.append("<tr>")
            for index, cell in enumerate(row):
                cls = ' class="date-column"' if index == 0 else ""
                output.append("<td{}>{}</td>".format(cls, render_inline(cell, table_cell=True)))
            output.append("</tr>")
        output.append("</tbody></table>")
        return "".join(output)

    output = ['<div class="md-table" role="table"><div class="md-table-row md-table-head" role="row">']
    output.extend(
        '<div role="columnheader">{}</div>'.format(render_inline(cell, table_cell=True))
        for cell in headers
    )
    output.append("</div>")
    for row in body:
        output.append('<div class="md-table-row" role="row">')
        output.extend(
            '<div role="cell">{}</div>'.format(render_inline(cell, table_cell=True))
            for cell in row
        )
        output.append("</div>")
    output.append("</div>")
    return "".join(output)


def render_list(lines: list[str]) -> str:
    roots: list[dict[str, Any]] = []
    base_indent: Optional[int] = None
    for line in lines:
        match = LIST_ITEM.match(line)
        if not match:
            continue
        indent = len(match.group(1).expandtabs(4))
        if base_indent is None:
            base_indent = indent
        item = {"text": match.group(2), "children": []}
        if indent <= base_indent or not roots:
            roots.append(item)
        else:
            roots[-1]["children"].append(item)

    def render_items(items: list[dict[str, Any]]) -> str:
        output = ["<ul>"]
        for item in items:
            output.append("<li>{}".format(render_inline(item["text"])))
            if item["children"]:
                output.append(render_items(item["children"]))
            output.append("</li>")
        output.append("</ul>")
        return "".join(output)

    return render_items(roots)


def render_markdown(markdown: str, style: str) -> str:
    lines = markdown.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if is_table_start(lines, index):
            table_lines = [line, lines[index + 1]]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            output.append(render_table(table_lines, style))
            continue
        if re.fullmatch(r"\s*---+\s*", line):
            output.append("<hr>")
            index += 1
            continue
        heading = HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            if level == 3 and style == "modern":
                date_index = index + 1
                while date_index < len(lines) and not lines[date_index].strip():
                    date_index += 1
                if date_index < len(lines):
                    date_line = lines[date_index].strip()
                    date_text = date_line[1:-1].strip() if date_line.startswith("*") and date_line.endswith("*") else date_line
                    if DATE_LINE.fullmatch(date_text):
                        output.append(
                            '<div class="entry-heading"><h3 class="entry-role">{}</h3>'
                            '<p class="date-line">{}</p></div>'.format(
                                render_inline(heading.group(2)),
                                render_inline(date_line),
                            )
                        )
                        index = date_index + 1
                        continue
            output.append(
                '<h{} class="section-heading">{}</h{}>'.format(
                    level, render_inline(heading.group(2)), level,
                )
            )
            index += 1
            continue
        if LIST_ITEM.match(line):
            list_lines = []
            while index < len(lines) and LIST_ITEM.match(lines[index]):
                list_lines.append(lines[index])
                index += 1
            output.append(render_list(list_lines))
            continue
        paragraph_lines = [line.strip()]
        index += 1
        while index < len(lines):
            if not lines[index].strip() or HEADING.match(lines[index]) or LIST_ITEM.match(lines[index]):
                break
            if re.fullmatch(r"\s*---+\s*", lines[index]) or is_table_start(lines, index):
                break
            paragraph_lines.append(lines[index].strip())
            index += 1
        paragraph = " ".join(paragraph_lines)
        date_text = paragraph[1:-1].strip() if paragraph.startswith("*") and paragraph.endswith("*") else paragraph
        paragraph_class = ' class="date-line"' if DATE_LINE.fullmatch(date_text) else ""
        output.append("<p{}>{}</p>".format(paragraph_class, render_inline(paragraph)))
    return "\n".join(output)


def contact_line(metadata: dict[str, Any]) -> str:
    fields = (
        ("email", metadata.get("email")),
        ("phone", metadata.get("phone")),
        ("location", metadata.get("location")),
        ("linkedin", metadata.get("linkedin")),
        ("github", metadata.get("github")),
        ("website", metadata.get("website")),
    )
    return " · ".join(html.escape(str(value)) for _, value in fields if value)


def personal_line(metadata: dict[str, Any], lang: str) -> str:
    parts = []
    birth_date = metadata.get("birth_date")
    nationality = metadata.get("nationality")
    if birth_date:
        label = "Geburtsdatum" if lang == "de" else "Birth date"
        parts.append("{}: {}".format(label, html.escape(str(birth_date))))
    if nationality:
        label = "Staatsangehörigkeit" if lang == "de" else "Nationality"
        parts.append("{}: {}".format(label, html.escape(str(nationality))))
    return " · ".join(parts)


def photo_data_uri(path: Optional[str]) -> str:
    if not path:
        return ""
    image_path = Path(path)
    mime = mimetypes.guess_type(image_path.name)[0] or ""
    if mime not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
        raise ValueError("--photo must be PNG, JPEG, GIF, or WebP")
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return "data:{};base64,{}".format(mime, encoded)


def render_cv_header(metadata: dict[str, Any], lang: str, photo: Optional[str]) -> str:
    name = html.escape(str(metadata.get("name") or ""))
    title = metadata.get("title")
    title_html = '<p class="cv-title">{}</p>'.format(html.escape(str(title))) if title else ""
    contacts = contact_line(metadata)
    contact_html = '<p class="contact-line">{}</p>'.format(contacts) if contacts else ""
    personal = personal_line(metadata, lang)
    personal_html = '<p class="personal-line">{}</p>'.format(personal) if personal else ""
    image_uri = photo_data_uri(photo)
    photo_html = ""
    if image_uri:
        alt = "Foto" if lang == "de" else "Portrait"
        photo_html = '<img class="cv-photo" src="{}" alt="{}">'.format(
            image_uri, html.escape(alt, quote=True),
        )
    return (
        '<header class="cv-header"><div class="cv-heading-text">'
        "<h1>{}</h1>{}{}{}</div>{}</header>"
    ).format(name, title_html, contact_html, personal_html, photo_html)


def render_letter_header(metadata: dict[str, Any], lang: str) -> str:
    sender = []
    if metadata.get("name"):
        sender.append("<strong>{}</strong>".format(html.escape(str(metadata["name"]))))
    if metadata.get("title"):
        sender.append(html.escape(str(metadata["title"])))
    if metadata.get("location"):
        sender.append(html.escape(str(metadata["location"])))
    if metadata.get("email"):
        sender.append(html.escape(str(metadata["email"])))
    if metadata.get("phone"):
        sender.append(html.escape(str(metadata["phone"])))
    sender_html = '<header class="letter-sender">{}</header>'.format(
        "<br>".join(sender),
    )
    recipients = metadata.get("recipient") or []
    if isinstance(recipients, str):
        recipients = recipients.splitlines()
    recipient_html = ""
    if recipients:
        recipient_html = '<address class="letter-recipient">{}</address>'.format(
            "<br>".join(html.escape(str(item)) for item in recipients),
        )
    date_html = ""
    if metadata.get("date"):
        date_html = '<p class="letter-date">{}</p>'.format(html.escape(str(metadata["date"])))
    subject_html = ""
    if metadata.get("subject"):
        label = "Betreff:" if lang == "de" else "Subject:"
        subject_html = '<p class="letter-subject"><strong>{} {}</strong></p>'.format(
            label, html.escape(str(metadata["subject"])),
        )
    return sender_html + recipient_html + date_html + subject_html


def stylesheet(lang: str) -> str:
    letter_spacing = "7mm" if lang == "de" else "5mm"
    path = Path(__file__).resolve().parent.parent / "assets" / "render-doc.css"
    return path.read_text(encoding="utf-8").replace("LETTER_SPACING", letter_spacing)


def render_html(
    document: str,
    document_type: str,
    style: str,
    lang: str,
    photo: Optional[str] = None,
) -> str:
    metadata, markdown = parse_front_matter(document)
    if document_type == "cv":
        header = render_cv_header(metadata, lang, photo)
        body = header + '<main class="cv-content">{}</main>'.format(render_markdown(markdown, style))
    else:
        if photo:
            raise ValueError("--photo is only supported for --type cv")
        header = render_letter_header(metadata, lang)
        body = header + '<main class="letter-body">{}</main>'.format(render_markdown(markdown, style))
    title = metadata.get("name") or ("Lebenslauf" if lang == "de" else "Curriculum Vitae")
    if document_type == "letter":
        title = metadata.get("subject") or ("Anschreiben" if lang == "de" else "Cover Letter")
    return (
        '<!doctype html><html lang="{}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>{}</title><style>{}</style></head><body>"
        '<article class="page document-{} style-{}">{}</article></body></html>'
    ).format(
        html.escape(lang, quote=True),
        html.escape(str(title)),
        stylesheet(lang),
        document_type,
        style,
        body,
    )


def find_chromium() -> Optional[str]:
    mac_paths = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    )
    for candidate in mac_paths:
        path = Path(candidate)
        if path.is_file() and path.stat().st_mode & 0o111:
            return str(path)
    for name in (
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
        "msedge", "msedge.exe", "brave", "brave-browser",
    ):
        path = shutil.which(name)
        if path:
            return path
    return None


def pdf_page_count(path: Path) -> int:
    contents = path.read_bytes()
    return len(re.findall(rb"/Type\s*/Page[^s]", contents))


def print_pdf(browser: str, html_path: Path, pdf_path: Path) -> tuple[bool, str]:
    protection = protect_if_inside_data_dir(pdf_path)
    if protection is not None:
        warn_data_dir_protection(protection)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    before = pdf_path.stat().st_mtime_ns if pdf_path.exists() else None
    failures = []
    for headless in ("--headless=new", "--headless"):
        command = [
            browser,
            headless,
            "--disable-gpu",
            "--no-pdf-header-footer",
            "--print-to-pdf={}".format(pdf_path.resolve()),
            html_path.resolve().as_uri(),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        except subprocess.TimeoutExpired:
            failures.append("{} timed out after 60 seconds".format(headless))
            continue
        after = pdf_path.stat().st_mtime_ns if pdf_path.exists() else None
        wrote_new_pdf = after is not None and (before is None or after != before)
        if result.returncode == 0 and wrote_new_pdf:
            return True, ""
        detail = result.stderr.strip() or result.stdout.strip() or "no new PDF was written"
        failures.append("{} failed ({}): {}".format(headless, result.returncode, detail))
    return False, "\n".join(failures)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render Markdown CVs and letters as print-ready HTML and optional PDF.")
    parser.add_argument("input", metavar="INPUT.md")
    parser.add_argument("--type", choices=("cv", "letter"), required=True)
    parser.add_argument("--style", choices=("lebenslauf", "modern"))
    parser.add_argument("--lang", choices=("de", "en"), default="de")
    parser.add_argument("--photo")
    parser.add_argument("--out", help="HTML output path (default: INPUT.html)")
    parser.add_argument("--pdf", help="Optional PDF output path")
    args = parser.parse_args(argv)
    try:
        input_path = Path(args.input)
        markdown = input_path.read_text(encoding="utf-8")
        style = args.style or ("lebenslauf" if args.lang == "de" else "modern")
        html_text = render_html(markdown, args.type, style, args.lang, args.photo)
        html_path = Path(args.out) if args.out else input_path.with_suffix(".html")
        protection = protect_if_inside_data_dir(html_path)
        if protection is not None:
            warn_data_dir_protection(protection)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html_text, encoding="utf-8")
        print("HTML: {}".format(html_path))
        if not args.pdf:
            return 0
        browser = find_chromium()
        if not browser:
            print("Open the HTML and use Print → Save as PDF (A4, no headers/footers)", file=sys.stderr)
            return 0
        pdf_path = Path(args.pdf)
        ok, error = print_pdf(browser, html_path, pdf_path)
        if not ok:
            print("error: could not print PDF:\n{}".format(error), file=sys.stderr)
            return 2
        pages = pdf_page_count(pdf_path)
        print("PDF: {} ({} page{})".format(pdf_path, pages, "" if pages == 1 else "s"))
        limit = 2 if args.type == "cv" else 1
        if pages > limit:
            print(
                "warning: {} is {} pages (recommended maximum: {})".format(
                    args.type, pages, limit,
                ),
                file=sys.stderr,
            )
        return 0
    except (OSError, UnicodeError, ValueError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
