from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.parse
from html.parser import HTMLParser
from typing import Any, Optional

from _common import (
    USER_AGENT,
    disabled_sources,
    json_text,
    request_bytes,
    request_json,
    salary_text,
    strip_html,
    write_output,
)
from analyze_jd import analyze, employment_type_from_title


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.json_ld: list[str] = []
        self.text: list[str] = []
        self._script_type = False
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        values = dict(attrs)
        if tag == "script" and "ld+json" in (values.get("type") or "").casefold():
            self._script_type = True
        if tag in {"script", "style", "noscript"}:
            self._hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._script_type = False
        if tag in {"script", "style", "noscript"} and self._hidden:
            self._hidden -= 1

    def handle_data(self, data: str) -> None:
        if self._script_type:
            self.json_ld.append(data)
        elif not self._hidden:
            self.text.append(data)


def jobposting_from(value: Any) -> Optional[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            found = jobposting_from(item)
            if found:
                return found
    elif isinstance(value, dict):
        types = value.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if "JobPosting" in types:
            return value
        for key in ("@graph", "mainEntity", "itemListElement"):
            found = jobposting_from(value.get(key))
            if found:
                return found
    return None


def extract_json_ld(html_text: str) -> Optional[dict[str, Any]]:
    parser = PageParser()
    parser.feed(html_text)
    for raw in parser.json_ld:
        try:
            found = jobposting_from(json.loads(raw))
            if found:
                return found
        except (ValueError, TypeError):
            continue
    return None


def value_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(value_text(item) for item in value if item)
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or "")
    return str(value or "")


def title_first_employment_type(title: Any, current: Any = None) -> Optional[str]:
    return employment_type_from_title(str(title or "")) or value_text(current) or None


def numeric_salary_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text if re.search(r"\d", text) else None


def jsonld_detail(data: dict[str, Any], url: str) -> dict[str, Any]:
    company = data.get("hiringOrganization") or {}
    company_name = value_text(company.get("name") if isinstance(company, dict) else company)
    title = value_text(data.get("title"))
    locations = data.get("jobLocation") or data.get("jobLocationType") or []
    if isinstance(locations, dict):
        locations = [locations]
    place_names = []
    for place in locations if isinstance(locations, list) else []:
        address = place.get("address", {}) if isinstance(place, dict) else {}
        if isinstance(address, dict):
            parts = [address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry")]
            place_names.append(", ".join(str(part) for part in parts if part))
    salary = data.get("baseSalary")
    salary_text = None
    if isinstance(salary, dict):
        amount = salary.get("value", {})
        if isinstance(amount, dict):
            minimum, maximum = amount.get("minValue"), amount.get("maxValue")
            unit = amount.get("unitText")
            salary_text = "{}{}{}".format(minimum or "", "–" if minimum and maximum else "", maximum or "")
            salary_text = "{} {} {}".format(salary_text, salary.get("currency", "EUR"), unit or "").strip()
        else:
            salary_text = value_text(amount)
    salary_text = numeric_salary_text(salary_text)
    raw_description = html.unescape(str(data.get("description") or ""))
    description = strip_html(raw_description)
    return {
        "title": title,
        "company": company_name,
        "location": " / ".join(filter(None, place_names)),
        "date_posted": value_text(data.get("datePosted")) or None,
        "valid_through": value_text(data.get("validThrough")) or None,
        "employment_type": title_first_employment_type(title, data.get("employmentType")),
        "base_salary": salary_text,
        "description_text": description,
        "url": url,
        "analysis": analyze(raw_description, title=title, employer_name=company_name),
    }


def api_fallback(url: str) -> Optional[dict[str, Any]]:
    parsed = urllib.parse.urlsplit(url)
    parts = [part for part in parsed.path.split("/") if part]
    host = parsed.netloc.casefold()
    item: Optional[dict[str, Any]] = None
    if "greenhouse.io" in host and len(parts) >= 3:
        board = parts[0]
        job_id = parts[-1]
        payload = request_json("https://boards-api.greenhouse.io/v1/boards/{}/jobs?content=true".format(urllib.parse.quote(board)))
        item = next((row for row in payload.get("jobs", []) if str(row.get("id")) == job_id), None)
        if item:
            raw_description = html.unescape(str(item.get("content") or ""))
            return {
                "title": item.get("title", ""), "company": board, "location": (item.get("location") or {}).get("name", ""),
                "date_posted": item.get("first_published"), "valid_through": None, "employment_type": None,
                "base_salary": None, "description_text": strip_html(raw_description),
                "_raw_description": raw_description,
                "url": item.get("absolute_url") or url,
            }
    if "lever.co" in host and parts:
        company = parts[0]
        payload = request_json("https://api.lever.co/v0/postings/{}?mode=json".format(urllib.parse.quote(company)))
        item = next((row for row in payload if row.get("hostedUrl", "").rstrip("/") == url.rstrip("/")), None)
        if item:
            categories = item.get("categories") or {}
            raw_description = item.get("descriptionPlain") or item.get("openingPlain") or ""
            return {
                "title": item.get("text", ""), "company": company, "location": categories.get("location", ""),
                "date_posted": item.get("createdAt"), "valid_through": None, "employment_type": categories.get("commitment"),
                "base_salary": None, "description_text": strip_html(raw_description),
                "_raw_description": raw_description,
                "url": item.get("hostedUrl") or url,
            }
    if "ashbyhq.com" in host and parts:
        board = parts[0]
        payload = request_json("https://api.ashbyhq.com/posting-api/job-board/{}?includeCompensation=true".format(urllib.parse.quote(board)))
        item = next((row for row in payload.get("jobs", []) if row.get("jobUrl", "").rstrip("/") == url.rstrip("/")), None)
        if item:
            raw_description = item.get("descriptionPlain") or item.get("descriptionHtml") or ""
            return {
                "title": item.get("title", ""), "company": board, "location": item.get("location", ""),
                "date_posted": item.get("publishedAt"), "valid_through": None,
                "employment_type": item.get("employmentType"), "base_salary": salary_text(item.get("compensation")),
                "description_text": strip_html(raw_description), "_raw_description": raw_description,
                "url": item.get("jobUrl") or url,
            }
    return None


def daily_berlin_job_id(address: str) -> Optional[str]:
    try:
        parsed = urllib.parse.urlsplit(address)
    except ValueError:
        return None
    if (parsed.hostname or "").casefold() != "daily-berlin-jobs.vercel.app":
        return None
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) == 2 and parts[0] == "jobs":
        identifier = parts[1][:-3] if parts[1].endswith(".md") else parts[1]
    elif len(parts) == 4 and parts[:3] == ["api", "v1", "jobs"]:
        identifier = parts[3]
    else:
        return None
    return identifier if re.fullmatch(r"\d+", identifier) else None


def is_linkedin_url(address: str) -> bool:
    try:
        host = urllib.parse.urlsplit(address).hostname or ""
    except ValueError:
        return False
    host = host.casefold()
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def fetch_linkedin_job_detail(address: str) -> dict[str, Any]:
    from linkedin import fetch_job_detail

    return fetch_job_detail(address)


def warn_tos() -> None:
    from linkedin import warn_tos as warn_linkedin_tos

    warn_linkedin_tos()


def linkedin_detail(address: str) -> dict[str, Any]:
    if "linkedin" in disabled_sources():
        raise ValueError("LinkedIn detail fetching is disabled in this package; open the posting in your browser.")
    warn_tos()
    detail = fetch_linkedin_job_detail(address)
    title = detail.get("title") or ""
    company = detail.get("company") or ""
    analysis = analyze(detail.get("description") or "", title=title, employer_name=company)
    existing_analysis = detail.get("analysis")
    if isinstance(existing_analysis, dict) and "integrity" in existing_analysis:
        analysis["integrity"] = existing_analysis["integrity"]
    return {
        "title": title,
        "company": company,
        "location": detail.get("location") or "",
        "date_posted": detail.get("date"),
        "valid_through": None,
        "employment_type": title_first_employment_type(title, detail.get("employment_type")),
        "base_salary": numeric_salary_text((analysis.get("salary") or {}).get("raw")),
        "description_text": detail.get("description") or "",
        "url": detail.get("url") or address,
        "analysis": analysis,
    }


def fetch_daily_berlin_job(address: str, job_id: str) -> dict[str, Any]:
    api_url = "https://daily-berlin-jobs.vercel.app/api/v1/jobs/{}".format(
        urllib.parse.quote(job_id, safe=""),
    )
    record = request_json(api_url)
    original_url = str(record.get("url") or "")
    raw_description = record.get("description")

    if isinstance(raw_description, str) and raw_description.strip():
        raw_description = html.unescape(raw_description)
        title = record.get("title") or ""
        company = record.get("company") or ""
        result = {
            "title": title,
            "company": company,
            "location": record.get("location") or "",
            "date_posted": record.get("posted_date"),
            "valid_through": None,
            "employment_type": record.get("employment_type"),
            "base_salary": None,
            "description_text": strip_html(raw_description),
            "url": original_url or address,
            "analysis": analyze(raw_description, title=title, employer_name=company),
        }
    elif is_linkedin_url(original_url):
        result = linkedin_detail(original_url)
    elif original_url:
        result = fetch_detail(original_url)
    else:
        result = {
            "title": record.get("title") or "",
            "company": record.get("company") or "",
            "location": record.get("location") or "",
            "date_posted": record.get("posted_date"),
            "valid_through": None,
            "employment_type": record.get("employment_type"),
            "base_salary": None,
            "description_text": "",
            "url": address,
            "analysis": analyze(
                "",
                title=record.get("title") or "",
                employer_name=record.get("company") or "",
            ),
        }

    result.update({
        "id": str(record.get("id") or job_id),
        "source": "dailyberlinjobs",
        "via": record.get("source"),
        "url": original_url or result.get("url") or address,
        "detail_url": record.get("detail_url") or api_url,
        "role": record.get("role"),
        "level": record.get("level"),
        "work_mode": record.get("work_mode"),
        "tech_stack": record.get("tech_stack") or [],
        "posted_date": record.get("posted_date"),
        "first_seen_at": record.get("first_seen_at"),
        "description_available": bool(record.get("description_available")),
    })
    result["title"] = record.get("title") or result.get("title") or ""
    result["company"] = record.get("company") or result.get("company") or ""
    result["location"] = record.get("location") or result.get("location") or ""
    result["date_posted"] = record.get("posted_date") or result.get("date_posted")
    result["employment_type"] = title_first_employment_type(
        result.get("title") or record.get("title"),
        record.get("employment_type") or result.get("employment_type"),
    )
    result["base_salary"] = numeric_salary_text(result.get("base_salary"))
    return result


def fetch_detail(address: str) -> dict[str, Any]:
    dbj_id = daily_berlin_job_id(address)
    if dbj_id is not None:
        return fetch_daily_berlin_job(address, dbj_id)
    if is_linkedin_url(address):
        return linkedin_detail(address)
    if re.fullmatch(r"\d{5,}-[A-Za-z0-9-]+", address):
        url = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{}".format(urllib.parse.quote(address, safe="-"))
    else:
        url = address
    status, body = request_bytes(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    if status < 200 or status >= 300:
        raise RuntimeError("HTTP {} fetching {}".format(status, url))
    page = body.decode("utf-8", "replace")
    posting = extract_json_ld(page)
    if posting:
        return jsonld_detail(posting, url)
    fallback = api_fallback(url)
    if fallback:
        raw_description = fallback.pop("_raw_description", fallback.get("description_text", ""))
        fallback["employment_type"] = title_first_employment_type(
            fallback.get("title"), fallback.get("employment_type"),
        )
        fallback["analysis"] = analyze(
            raw_description,
            title=fallback.get("title") or "",
            employer_name=fallback.get("company") or "",
        )
        fallback["base_salary"] = numeric_salary_text(fallback.get("base_salary"))
        return fallback
    parser = PageParser()
    parser.feed(page)
    description = " ".join(" ".join(parser.text).split())
    title_match = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    title = strip_html(title_match.group(1)) if title_match else ""
    return {
        "title": title, "company": "", "location": "", "date_posted": None, "valid_through": None,
        "employment_type": title_first_employment_type(title), "base_salary": None, "description_text": description[:20000],
        "url": url, "analysis": analyze(page, title=title),
    }


def markdown(detail: dict[str, Any]) -> str:
    lines = ["# {}".format(detail.get("title") or "Job detail")]
    for key, label in (("company", "Company"), ("location", "Location"), ("date_posted", "Posted"),
                       ("valid_through", "Valid through"), ("employment_type", "Employment type"),
                       ("base_salary", "Base salary"), ("url", "URL")):
        lines.append("- **{}:** {}".format(label, detail.get(key) or "—"))
    for key, label in (("via", "Daily Berlin Jobs source"), ("role", "Role"), ("level", "Level"),
                       ("work_mode", "Work mode"), ("tech_stack", "Tech stack"),
                       ("detail_url", "Daily Berlin Jobs detail URL")):
        if key in detail:
            value = detail.get(key)
            if isinstance(value, list):
                value = ", ".join(str(item) for item in value)
            lines.append("- **{}:** {}".format(label, value or "—"))
    lines.extend(["", "## Description", detail.get("description_text") or "No readable description found.",
                  "", "## Analysis", "```json", json_text(detail["analysis"]), "```"])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch one job posting and analyze its description.")
    parser.add_argument("url_or_ba_refnr", metavar="URL_OR_BA_REFNR",
                        help="Posting URL or Bundesagentur für Arbeit reference number")
    parser.add_argument("--format", choices=("json", "md"), default="json", metavar="json|md")
    args = parser.parse_args()
    try:
        detail = fetch_detail(args.url_or_ba_refnr)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    output = json_text(detail) if args.format == "json" else markdown(detail)
    write_output(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
