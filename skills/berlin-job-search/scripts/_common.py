from __future__ import annotations

import csv
import datetime as dt
import http.client
import html
import ipaddress
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

USER_AGENT = "berlin-job-search-skill/0.1 (+https://github.com/umutyesildal/berlin-job-search)"

_DATA_DIR_PROTECTION_CACHE: dict[str, dict[str, Any]] = {}
_DATA_DIR_WARNINGS_PRINTED: set[str] = set()


def default_data_dir() -> Path:
    return Path(os.environ.get("GJS_DATA_DIR") or "./job-search")


def disabled_sources() -> set[str]:
    build_file = Path(__file__).resolve().parent.parent / "assets" / "build.json"
    if not build_file.is_file():
        return set()
    try:
        build = json.loads(build_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid assets/build.json: {}".format(error)) from error
    sources = build.get("disabled_sources") if isinstance(build, dict) else None
    if not isinstance(sources, list) or any(not isinstance(source, str) for source in sources):
        raise ValueError("assets/build.json disabled_sources must be a list of source names")
    return {source.strip().casefold() for source in sources if source.strip()}


def protect_data_dir(path: Any) -> dict[str, Any]:
    root = Path(path).expanduser().resolve()
    cache_key = str(root)
    if cache_key in _DATA_DIR_PROTECTION_CACHE:
        return _DATA_DIR_PROTECTION_CACHE[cache_key]

    root.mkdir(parents=True, exist_ok=True)
    worktree = next(
        (candidate for candidate in (root, *root.parents) if (candidate / ".git").exists()),
        None,
    )
    if worktree is None:
        result = {
            "path": str(root),
            "in_git_worktree": False,
            "worktree": None,
            "protected": True,
            "gitignore_created": False,
            "tracked_files": [],
            "warnings": [],
            "tracked_check": "not_needed",
        }
        _DATA_DIR_PROTECTION_CACHE[cache_key] = result
        return result

    warnings: list[str] = []
    gitignore_created = False
    protected = worktree != root
    if worktree == root:
        warnings.append(
            "The data directory is the root of a Git repository; choose a subfolder or a folder outside the repository."
        )
    else:
        gitignore = root / ".gitignore"
        if not gitignore.exists():
            try:
                gitignore.write_text(
                    "# Created by Berlin Job Search: personal job-search data, never commit it.\n*\n",
                    encoding="utf-8",
                )
            except OSError as error:
                protected = False
                warnings.append("Could not create {}: {}".format(gitignore, error))
            else:
                gitignore_created = True
        elif not gitignore.is_file():
            protected = False
            warnings.append("Add a line '*' to {} to protect this data directory.".format(gitignore))
        else:
            try:
                lines = gitignore.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError) as error:
                protected = False
                warnings.append("Could not read {}: {}".format(gitignore, error))
            else:
                has_wildcard = False
                has_exception = False
                for line in lines:
                    stripped = line.lstrip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    if line.strip() == "*":
                        has_wildcard = True
                    if stripped.startswith("!"):
                        has_exception = True
                if has_exception:
                    protected = False
                    warnings.append(
                        "{} contains a Git ignore exception; remove the exception or choose a data "
                        "directory outside the repository to protect personal data.".format(gitignore)
                    )
                if not has_wildcard:
                    protected = False
                    warnings.append("Add a line '*' to {} to protect this data directory.".format(gitignore))

    git = shutil.which("git")
    tracked_files: list[str] = []
    if git is None:
        tracked_check = "skipped"
    else:
        try:
            tracked_result = subprocess.run(
                [git, "-C", str(root), "ls-files", "-z", "--", "."],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            tracked_check = "unknown"
            protected = False
            warnings.append("Could not check tracked files in {}: {}".format(root, error))
        else:
            if tracked_result.returncode != 0:
                tracked_check = "unknown"
                protected = False
                warnings.append("Could not check tracked files in {}; Git returned an error.".format(root))
            else:
                tracked_check = "done"
                paths = [
                    os.fsdecode(value)
                    for value in tracked_result.stdout.split(b"\0")
                    if value
                ]
                relative_root = root.relative_to(worktree).as_posix()
                if relative_root != ".":
                    prefix = relative_root.rstrip("/") + "/"
                    paths = [
                        value[len(prefix):] if value.startswith(prefix) else value
                        for value in paths
                    ]
                if paths:
                    protected = False
                    warnings.append(
                        "{} file(s) in {} are already tracked by Git; ignore rules don't protect them. "
                        "To stop tracking without deleting your local copies: git rm --cached -r -- {} (then commit)."
                        .format(len(paths), root, root)
                    )
                    tracked_files = paths[:20]

    if git is not None:
        try:
            unignored_result = subprocess.run(
                [git, "-C", str(root), "ls-files", "-z", "--others", "--exclude-standard", "--", "."],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            protected = False
            warnings.append("Could not check unignored files in {}: {}".format(root, error))
        else:
            if unignored_result.returncode != 0:
                protected = False
                warnings.append(
                    "Could not check unignored files in {}; Git returned an error.".format(root)
                )
            else:
                unignored_files = [
                    os.fsdecode(value)
                    for value in unignored_result.stdout.split(b"\0")
                    if value
                ]
                if unignored_files:
                    protected = False
                    shown_paths = ", ".join(unignored_files[:5])
                    more = " (+{} more)".format(len(unignored_files) - 5) if len(unignored_files) > 5 else ""
                    warnings.append(
                        "Git can see unignored file(s) in {}: {}{}. Remove ignore exceptions or choose "
                        "a data directory outside the repository.".format(root, shown_paths, more)
                    )

    result = {
        "path": str(root),
        "in_git_worktree": True,
        "worktree": str(worktree),
        "protected": protected,
        "gitignore_created": gitignore_created,
        "tracked_files": tracked_files,
        "warnings": warnings,
        "tracked_check": tracked_check,
    }
    _DATA_DIR_PROTECTION_CACHE[cache_key] = result
    return result


def warn_data_dir_protection(result: dict[str, Any]) -> None:
    for warning in result.get("warnings", []):
        if warning not in _DATA_DIR_WARNINGS_PRINTED:
            print("warning: {}".format(warning), file=sys.stderr)
            _DATA_DIR_WARNINGS_PRINTED.add(warning)


def protect_if_inside_data_dir(target: Any) -> Optional[dict[str, Any]]:
    try:
        data_root = default_data_dir().resolve()
        output_path = Path(target).resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    if output_path == data_root or data_root in output_path.parents:
        return protect_data_dir(default_data_dir())
    return None


CITY_ALIASES = {
    "munich": {
        "key": "munich",
        "canonical": "Munich",
        "ba": "München",
        "linkedin": "Munich, Germany",
        "geo_id": "100495942",
        "variants": ("München", "Munich", "Muenchen"),
    },
    "cologne": {
        "key": "cologne",
        "canonical": "Cologne",
        "ba": "Köln",
        "linkedin": "Cologne, Germany",
        "geo_id": "90009713",
        "variants": ("Köln", "Cologne", "Koeln", "Cologne/Bonn"),
    },
    "nuremberg": {
        "key": "nuremberg",
        "canonical": "Nuremberg",
        "ba": "Nürnberg",
        "linkedin": "Nuremberg, Germany",
        "geo_id": None,
        "variants": ("Nürnberg", "Nuremberg", "Nuernberg"),
    },
    "frankfurt": {
        "key": "frankfurt",
        "canonical": "Frankfurt",
        "ba": "Frankfurt am Main",
        "linkedin": "Frankfurt am Main, Germany",
        "geo_id": "90009714",
        "variants": ("Frankfurt", "Frankfurt am Main", "Frankfurt/Rhine-Main"),
    },
    "dusseldorf": {
        "key": "dusseldorf",
        "canonical": "Düsseldorf",
        "ba": "Düsseldorf",
        "linkedin": "Düsseldorf, Germany",
        "geo_id": None,
        "variants": ("Düsseldorf", "Duesseldorf"),
    },
    "hamburg": {
        "key": "hamburg",
        "canonical": "Hamburg",
        "ba": "Hamburg",
        "linkedin": "Hamburg, Germany",
        "geo_id": "101949806",
        "variants": ("Hamburg",),
    },
    "berlin": {
        "key": "berlin",
        "canonical": "Berlin",
        "ba": "Berlin",
        "linkedin": "Berlin, Germany",
        "geo_id": "103035651",
        "variants": ("Berlin",),
    },
    "stuttgart": {
        "key": "stuttgart",
        "canonical": "Stuttgart",
        "ba": "Stuttgart",
        "linkedin": "Stuttgart, Germany",
        "geo_id": None,
        "variants": ("Stuttgart",),
    },
    "leipzig": {
        "key": "leipzig",
        "canonical": "Leipzig",
        "ba": "Leipzig",
        "linkedin": "Leipzig, Germany",
        "geo_id": None,
        "variants": ("Leipzig",),
    },
    "dresden": {
        "key": "dresden",
        "canonical": "Dresden",
        "ba": "Dresden",
        "linkedin": "Dresden, Germany",
        "geo_id": None,
        "variants": ("Dresden",),
    },
    "hannover": {
        "key": "hannover",
        "canonical": "Hanover",
        "ba": "Hannover",
        "linkedin": "Hanover, Germany",
        "geo_id": None,
        "variants": ("Hannover", "Hanover"),
    },
    "germany": {
        "key": "germany",
        "canonical": "Germany",
        "ba": "Deutschland",
        "linkedin": "Germany",
        "geo_id": "101282230",
        "variants": ("Germany", "Deutschland"),
    },
}


def _normalized_city_text(value: Any) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "").casefold())
    unaccented = "".join(char for char in folded if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", unaccented))


def city_alias(value: Any) -> Optional[dict[str, Any]]:
    normalized = _normalized_city_text(value)
    if not normalized:
        return None
    for alias in CITY_ALIASES.values():
        for variant in alias["variants"]:
            candidate = _normalized_city_text(variant)
            if normalized == candidate or normalized.startswith(candidate + " "):
                return alias
    return None


def ba_location_name(value: Any) -> str:
    alias = city_alias(value)
    return str(alias["ba"]) if alias else str(value or "")


def canonical_location_name(value: Any) -> str:
    alias = city_alias(value)
    if alias:
        return str(alias["canonical"])
    return str(value or "")


def linkedin_location_and_geo_id(value: Any) -> tuple[str, Optional[str]]:
    alias = city_alias(value)
    if alias:
        return str(alias["linkedin"]), alias.get("geo_id")
    return str(value or ""), None


def city_location_matches(query: Any, location: Any) -> bool:
    if not query:
        return True
    alias = city_alias(query)
    variants = alias["variants"] if alias else (str(query),)
    normalized_location = " " + _normalized_city_text(location) + " "
    return any(
        " " + _normalized_city_text(variant) + " " in normalized_location
        for variant in variants
        if _normalized_city_text(variant)
    )


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def strip_html(text: str) -> str:
    parser = TextExtractor()
    try:
        parser.feed(text)
        return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
    except Exception:
        return re.sub(r"<[^>]+>", " ", text)


def unescape_html(text: str) -> str:
    return html.unescape(text)


def clean_html_fragment(markup: str) -> str:
    return unescape_html(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup).strip()))


def escape_html(value: Any, *, quote: bool = True) -> str:
    return html.escape(str(value), quote=quote)


def plain(value: Any) -> str:
    return "" if value is None else strip_html(html.unescape(str(value)))


# Allow large public job boards while keeping every response bounded (20 MB).
MAX_RESPONSE_BYTES = 20_000_000
MAX_REDIRECTS = 5


def _is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return address.is_global and not address.is_multicast and not address.is_reserved


def public_url(url: str):
    """Validate syntax before opening any connection (including redirects)."""
    if not url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ValueError("Public HTTP(S) URL required")
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or "%" in parsed.hostname or "\\" in parsed.netloc):
        raise ValueError("Public HTTP(S) URL without credentials required")
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid URL port")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not _is_public_address(str(address)):
        raise ValueError("Non-public destination refused")
    return parsed


def _public_socket(host: str, port: int, timeout: float):
    # Resolve once, validate every answer, then connect to a numeric address.
    # Keeping DNS out of the actual connect prevents a second-resolution rebind.
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not answers or any(not _is_public_address(a[4][0]) for a in answers):
        raise ValueError("Non-public destination refused")
    last_error = None
    for family, socktype, protocol, _, address in answers:
        connection = socket.socket(family, socktype, protocol)
        try:
            connection.settimeout(timeout)
            connection.connect(address)
            return connection
        except OSError as error:
            connection.close()
            last_error = error
    raise last_error or OSError("No public destination available")


class _PublicHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = _public_socket(self.host, self.port, self.timeout)


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        connection = _public_socket(self.host, self.port, self.timeout)
        try:
            self.sock = self._context.wrap_socket(connection, server_hostname=self.host)
        except Exception:
            connection.close()
            raise


def request_bytes(
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 20,
) -> tuple[int, bytes]:
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*"}
    if headers:
        request_headers.update(headers)
    for redirect in range(MAX_REDIRECTS + 1):
        parsed = public_url(url)
        cls = _PublicHTTPSConnection if parsed.scheme == "https" else _PublicHTTPConnection
        connection = cls(parsed.hostname, parsed.port, timeout=timeout)
        try:
            target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            connection.request("GET", target, headers=request_headers)
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location or redirect == MAX_REDIRECTS:
                    raise ValueError("Missing redirect target or too many redirects")
                next_url = urllib.parse.urljoin(url, location)
                next_parsed = public_url(next_url)
                if parsed.scheme == "https" and next_parsed.scheme != "https":
                    raise ValueError("HTTPS downgrade refused")
                if (parsed.scheme, parsed.hostname, parsed.port) != (next_parsed.scheme, next_parsed.hostname, next_parsed.port):
                    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*"}
                url = next_url
                continue
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("Response exceeds size limit")
            return response.status, body
        finally:
            connection.close()
    raise ValueError("Too many redirects")


def request_json(url: str, *, headers: Optional[dict[str, str]] = None) -> Any:
    status, body = request_bytes(url, headers=headers)
    if status < 200 or status >= 300:
        raise RuntimeError("HTTP {}".format(status))
    return json.loads(body.decode("utf-8-sig"))


def salary_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, dict):
        minimum = value.get("minValue") or value.get("min")
        maximum = value.get("maxValue") or value.get("max")
        currency = value.get("currency") or value.get("currencyCode") or "EUR"
        if minimum is not None and maximum is not None:
            value = "{}–{} {}".format(minimum, maximum, currency)
        elif minimum is not None:
            value = "from {} {}".format(minimum, currency)
        else:
            value = value.get("label") or value.get("summary") or ""
    text = plain(value)
    if text.casefold() in {"", "keine_angaben", "keine angaben", "not disclosed", "n/a", "none"}:
        return None
    return text if re.search(r"\d", text) else None


def source_date(value: Any) -> Optional[str]:
    if not value:
        return None
    text = str(value).strip()
    try:
        if re.fullmatch(r"\d{10,13}", text):
            stamp = int(text) // 1000 if len(text) == 13 else int(text)
            return dt.datetime.fromtimestamp(stamp, dt.timezone.utc).date().isoformat()
        if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text):
            return dt.datetime.strptime(text, "%d.%m.%Y").date().isoformat()
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def parse_date_or_none(value: Any) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(value or ""))
    except ValueError:
        return None


def canonical_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in {"fbclid", "gclid"}
    ]
    return urllib.parse.urlunsplit((
        parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path.rstrip("/"),
        urllib.parse.urlencode(sorted(query)), "",
    ))


def normalize_identity(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    text = "".join(char for char in folded if not unicodedata.combining(char))
    text = re.sub(r"\((?:m\s*/\s*w\s*/\s*d|f\s*/\s*m\s*/\s*x|all genders)\)", " ", text, flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", text)


def slugify(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")[:60] or "job"


def json_text(value: Any, *, indent: Optional[int] = 2, sort_keys: bool = False) -> str:
    return json.dumps(value, ensure_ascii=False, indent=indent, sort_keys=sort_keys)


def spreadsheet_cell(value: Any) -> Any:
    if isinstance(value, str) and (value.startswith("'") or value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))):
        return "'" + value
    return value


def csv_text(rows: list[dict[str, Any]], fields: list[str]) -> str:
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows({key: spreadsheet_cell(value) for key, value in row.items()} for row in rows)
    return stream.getvalue()


def markdown_table(
    headers: list[str],
    rows: list[list[Any]],
    *,
    separators: Optional[list[str]] = None,
    spaced_separator: bool = False,
    normalize_newlines: bool = True,
) -> str:
    divider = separators or ["---"] * len(headers)
    if spaced_separator:
        lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(divider) + " |"]
    else:
        lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(divider) + "|"]
    for row in rows:
        cells = []
        for value in row:
            cell = str(value or "").replace("|", "\\|")
            if normalize_newlines:
                cell = cell.replace("\n", " ")
            cells.append(cell)
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_output(text: str, path: Optional[str] = None) -> None:
    if path:
        protection = protect_if_inside_data_dir(path)
        if protection is not None:
            warn_data_dir_protection(protection)
    print(text)
    if path:
        Path(path).write_text(text, encoding="utf-8")
