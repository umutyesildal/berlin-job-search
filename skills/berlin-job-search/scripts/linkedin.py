from __future__ import annotations

import argparse
import random
import re
import sys
import time
import urllib.parse
from typing import Any, Optional

from _common import (
    clean_html_fragment,
    json_text,
    linkedin_location_and_geo_id,
    markdown_table,
    request_bytes,
    unescape_html,
    write_output,
)
from analyze_jd import analyze, employment_type_from_title, seniority_from_title

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)
PAGE_SIZE = 10
MAX_PAGES = 5
EXPERIENCE_FLAGS = {
    "internship": "1",
    "entry": "2",
    "associate": "3",
    "mid-senior": "4",
    "director": "5",
    "executive": "6",
}
JOB_TYPE_FLAGS = {
    "full-time": "F",
    "part-time": "P",
    "contract": "C",
    "temporary": "T",
    "internship": "I",
}
_LAST_REQUEST_START: Optional[float] = None


class LinkedInRequestError(RuntimeError):
    pass


class LinkedInBlockedError(LinkedInRequestError):
    pass


def _pace() -> None:
    global _LAST_REQUEST_START
    now = time.monotonic()
    if _LAST_REQUEST_START is not None:
        delay = random.uniform(2.0, 4.0) - (now - _LAST_REQUEST_START)
        if delay > 0:
            time.sleep(delay)
    _LAST_REQUEST_START = time.monotonic()


def fetch_html(url: str, allow_not_found: bool = False) -> str:
    for attempt in range(3):
        _pace()
        try:
            status, body = request_bytes(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "X-Requested-With": "XMLHttpRequest",
            })
            if 200 <= status < 300:
                return body.decode("utf-8", "replace")
            if status == 999:
                raise LinkedInBlockedError(
                    "LinkedIn denied guest access (HTTP 999); stop this source and continue with other job sources"
                )
            if status == 404 and allow_not_found:
                return ""
            if status == 429 or status >= 500:
                if attempt < 2:
                    time.sleep((2 ** attempt) + random.uniform(0.0, 0.5))
                    continue
                raise LinkedInRequestError(
                    "LinkedIn request failed after two retries: HTTP {}".format(status)
                )
            raise LinkedInRequestError("LinkedIn request failed: HTTP {}".format(status))
        except OSError as error:
            raise LinkedInRequestError("Could not reach LinkedIn guest endpoint: {}".format(getattr(error, "reason", error))) from error
    raise LinkedInRequestError("LinkedIn request failed after two retries")


def _decode_entities(text: str) -> str:
    return unescape_html(text)


def _clean(markup: str) -> str:
    return clean_html_fragment(markup)


def parse_job_cards(markup: str) -> list[dict[str, Any]]:
    results = []
    chunks = re.split(r'data-entity-urn="urn:li:jobPosting:', markup)[1:]
    for chunk in chunks:
        identifier = re.match(r"(\d+)", chunk)
        if not identifier:
            continue
        job_id = identifier.group(1)
        link = re.search(
            r'class="base-card__full-link[^"]*"[^>]*href="([^"]+)"',
            chunk,
            re.I,
        )
        title_match = re.search(
            r'class="base-search-card__title"[^>]*>([\s\S]*?)</h3>',
            chunk,
            re.I,
        )
        if not title_match:
            title_match = re.search(r'class="sr-only"[^>]*>([\s\S]*?)</span>', chunk, re.I)
        title = _clean(title_match.group(1)) if title_match else ""
        if not title:
            continue
        subtitle = re.search(
            r'class="base-search-card__subtitle"[^>]*>([\s\S]*?)</h4>',
            chunk,
            re.I,
        )
        company = _clean(subtitle.group(1)) if subtitle else ""
        company_url = None
        if subtitle:
            company_link = re.search(r'href="([^"]+)"', subtitle.group(1), re.I)
            if company_link:
                company_url = _decode_entities(company_link.group(1)).split("?")[0]
        location_match = re.search(
            r'class="job-search-card__location"[^>]*>([\s\S]*?)</span>',
            chunk,
            re.I,
        )
        date_match = re.search(
            r'class="job-search-card__listdate[^"]*"[^>]*datetime="([^"]+)"',
            chunk,
            re.I,
        )
        job_url = _decode_entities(link.group(1)).split("?")[0] if link else ""
        results.append({
            "id": job_id,
            "title": title,
            "company": company or None,
            "company_url": company_url,
            "location": _clean(location_match.group(1)) if location_match else None,
            "date": date_match.group(1) if date_match else None,
            "url": job_url or "https://www.linkedin.com/jobs/view/{}".format(job_id),
        })
    return results


def build_search_url(
    location: str,
    query: Optional[str],
    days: Optional[int],
    minutes: Optional[int],
    remote: Optional[str],
    experience: Optional[str],
    job_type: Optional[str],
    easy_apply: bool,
    geo_id: Optional[str],
    page: int,
) -> str:
    normalized_location, alias_geo_id = linkedin_location_and_geo_id(location)
    params = {
        "location": normalized_location,
        "start": str((page - 1) * PAGE_SIZE),
    }
    if query:
        params["keywords"] = query
    resolved_geo_id = geo_id or alias_geo_id
    if resolved_geo_id:
        params["geoId"] = resolved_geo_id
    if minutes is not None:
        params["f_TPR"] = "r{}".format(minutes * 60)
    elif days is not None:
        params["f_TPR"] = "r{}".format(days * 86400)
    if remote:
        params["f_WT"] = {"onsite": "1", "remote": "2", "hybrid": "3"}[remote]
    if experience:
        values = _mapped_values(experience, EXPERIENCE_FLAGS, "experience")
        params["f_E"] = ",".join(values)
    if job_type:
        values = _mapped_values(job_type, JOB_TYPE_FLAGS, "job type")
        params["f_JT"] = ",".join(values)
    if easy_apply:
        params["f_AL"] = "true"
    return SEARCH_URL + "?" + urllib.parse.urlencode(params)


def _mapped_values(value: str, mapping: dict[str, str], label: str) -> list[str]:
    values = []
    for item in value.split(","):
        name = item.strip().casefold()
        if not name:
            continue
        if name not in mapping:
            raise ValueError("unknown {} {!r}".format(label, item.strip()))
        values.append(mapping[name])
    if not values:
        raise ValueError("{} filter must contain at least one value".format(label))
    return values


def search_jobs_with_errors(
    location: str,
    query: Optional[str] = None,
    days: Optional[int] = None,
    minutes: Optional[int] = None,
    remote: Optional[str] = None,
    experience: Optional[str] = None,
    job_type: Optional[str] = None,
    easy_apply: bool = False,
    geo_id: Optional[str] = None,
    pages: int = 1,
    limit: Optional[int] = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    if pages < 1 or pages > MAX_PAGES:
        raise ValueError("pages must be between 1 and {}".format(MAX_PAGES))
    if days is not None and minutes is not None:
        raise ValueError("choose --days or --minutes, not both")
    if remote and remote not in {"remote", "hybrid", "onsite"}:
        raise ValueError("remote must be remote, hybrid, or onsite")
    results = []
    errors = []
    for page in range(1, pages + 1):
        url = build_search_url(
            location, query, days, minutes, remote, experience, job_type,
            easy_apply, geo_id, page,
        )
        try:
            batch = parse_job_cards(fetch_html(url))
        except LinkedInRequestError as error:
            if not results:
                raise
            errors.append("page {}: {}".format(page, error))
            break
        if limit is not None:
            remaining = max(limit - len(results), 0)
            batch = batch[:remaining]
        results.extend(batch)
        if not batch or len(batch) < PAGE_SIZE or (limit is not None and len(results) >= limit):
            break
    return results, errors


def search_jobs(
    location: str,
    query: Optional[str] = None,
    days: Optional[int] = None,
    minutes: Optional[int] = None,
    remote: Optional[str] = None,
    experience: Optional[str] = None,
    job_type: Optional[str] = None,
    easy_apply: bool = False,
    geo_id: Optional[str] = None,
    pages: int = 1,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    results, errors = search_jobs_with_errors(
        location, query, days, minutes, remote, experience, job_type,
        easy_apply, geo_id, pages, limit,
    )
    if errors:
        raise LinkedInRequestError("; ".join(errors))
    return results


def extract_div_content(markup: str, class_name: str) -> Optional[str]:
    escaped = re.escape(class_name)
    opening = re.search(r'<div[^>]*class="[^"]*{}[^"]*"[^>]*>'.format(escaped), markup, re.I)
    if not opening:
        return None
    cursor = opening.end()
    depth = 1
    while depth > 0 and cursor < len(markup):
        next_open = markup.find("<div", cursor)
        next_close = markup.find("</div>", cursor)
        if next_close < 0:
            return None
        if next_open >= 0 and next_open < next_close:
            depth += 1
            cursor = next_open + 4
        else:
            depth -= 1
            cursor = next_close + 6
    return markup[opening.end():cursor - 6]


def normalize_job_id(value: str) -> Optional[str]:
    value = value.strip()
    urn = re.fullmatch(r"urn:li:jobPosting:(\d+)", value)
    if urn:
        return urn.group(1)
    if re.fullmatch(r"\d{6,}", value):
        return value
    has_scheme = re.match(r"^https?://", value, re.I)
    if has_scheme or re.match(r"^[a-z0-9.-]+\.[a-z]{2,}(/|$)", value, re.I):
        try:
            parsed = urllib.parse.urlsplit(value if has_scheme else "https://" + value)
        except ValueError:
            return None
        host = parsed.hostname or ""
        if host.casefold() != "linkedin.com" and not host.casefold().endswith(".linkedin.com"):
            return None
        match = re.search(r"/jobs/view/(?:[^/]*-)?(\d{6,})/?$", parsed.path)
        return match.group(1) if match else None
    slug = re.fullmatch(r"[a-z0-9-]*-(\d{6,})", value, re.I)
    return slug.group(1) if slug else None


def parse_job_detail(markup: str, job_id: str) -> dict[str, Any]:
    title_match = re.search(
        r'class="(?:top-card-layout__title|topcard__title)[^"]*"[^>]*>([\s\S]*?)</h[12]>',
        markup,
        re.I,
    )
    title = _clean(title_match.group(1)) if title_match else "(untitled)"
    company_match = re.search(
        r'class="topcard__org-name-link[^"]*"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>',
        markup,
        re.I,
    )
    location_match = re.search(
        r'class="topcard__flavor topcard__flavor--bullet"[^>]*>([\s\S]*?)</span>',
        markup,
        re.I,
    )
    description_html = (
        extract_div_content(markup, "show-more-less-html__markup")
        or extract_div_content(markup, "description__text")
    )
    description = None
    if description_html is not None:
        with_breaks = re.sub(r"<\s*br\s*/?>", "\n", description_html, flags=re.I)
        with_breaks = re.sub(r"</(p|li|ul|ol|div|h\d)>", "\n", with_breaks, flags=re.I)
        description = "\n".join(_clean(line) for line in with_breaks.split("\n"))
        description = re.sub(r"\n{3,}", "\n\n", description).strip() or None
    criteria = {}
    item_pattern = re.compile(
        r'class="description__job-criteria-subheader"[^>]*>([\s\S]*?)</h3>'
        r'[\s\S]*?class="description__job-criteria-text[^"]*"[^>]*>([\s\S]*?)</span>',
        re.I,
    )
    for match in item_pattern.finditer(markup):
        criteria[_clean(match.group(1)).casefold()] = _clean(match.group(2))
    description_start = re.search(
        r'class="(?:show-more-less-html__markup|description__text)',
        markup,
        re.I,
    )
    top_card = markup[:description_start.start()] if description_start else markup
    is_active = not re.search(
        r"closed-job__flavor|no longer accepting applications",
        top_card,
        re.I,
    )
    description_text = description or ""
    function = criteria.get("job function")
    industries = criteria.get("industries")
    seniority = seniority_from_title(title) or criteria.get("seniority level")
    employment = employment_type_from_title(title) or criteria.get("employment type")
    return {
        "id": job_id,
        "title": title,
        "company": _clean(company_match.group(2)) if company_match else None,
        "company_url": _decode_entities(company_match.group(1)).split("?")[0] if company_match else None,
        "location": _clean(location_match.group(1)) if location_match else None,
        "date": None,
        "url": "https://www.linkedin.com/jobs/view/{}".format(job_id),
        "description": description,
        "seniority": seniority,
        "employment_type": employment,
        "job_function": function,
        "industries": industries,
        "criteria": {
            "seniority": seniority,
            "employment_type": employment,
            "job_function": function,
            "industries": industries,
        },
        "is_active": bool(is_active),
        "analysis": analyze(description_html or "", title=title),
    }


def fetch_job_detail(job_id_or_url: str) -> dict[str, Any]:
    job_id = normalize_job_id(job_id_or_url)
    if not job_id:
        raise ValueError("could not parse a LinkedIn job ID from {!r}".format(job_id_or_url))
    markup = fetch_html("{}/{}".format(DETAIL_URL, job_id), allow_not_found=True)
    if not markup:
        raise LinkedInRequestError("LinkedIn job {} was not found".format(job_id))
    return parse_job_detail(markup, job_id)


def _search_markdown(results: list[dict[str, Any]]) -> str:
    rows = [
        [job["id"], job["title"], job.get("company") or "", job.get("location") or "",
         job.get("date") or "", job["url"]]
        for job in results
    ]
    return markdown_table(["ID", "Title", "Company", "Location", "Date", "URL"], rows, normalize_newlines=False)


def _detail_markdown(job: dict[str, Any]) -> str:
    lines = ["# {}".format(job.get("title") or "LinkedIn job")]
    for key, label in (("company", "Company"), ("location", "Location"),
                       ("seniority", "Seniority"), ("employment_type", "Employment type"),
                       ("job_function", "Job function"), ("industries", "Industries"),
                       ("url", "URL")):
        lines.append("- **{}:** {}".format(label, job.get(key) or "—"))
    lines.extend(["", "## Description", job.get("description") or "No readable description found.",
                  "", "## Analysis", "```json",
                  json_text(job.get("analysis", {})), "```"])
    return "\n".join(lines)


def warn_tos() -> None:
    print(
        "LinkedIn guest access is personal-use only, low-volume; automated access violates LinkedIn's User Agreement.",
        file=sys.stderr,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Low-volume search of public LinkedIn job listings.")
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="Search public job cards")
    search.add_argument("--location", required=True, help="LinkedIn location, e.g. Berlin, Germany")
    search.add_argument("--query", help="Keyword search")
    freshness = search.add_mutually_exclusive_group()
    freshness.add_argument("--days", type=int, choices=(1, 7, 14, 30), help="Posting age window")
    freshness.add_argument("--minutes", type=int, help="Posting age window in minutes")
    search.add_argument("--remote", choices=("remote", "hybrid", "onsite"))
    search.add_argument("--experience", help="Comma-separated: internship,entry,associate,mid-senior,director,executive")
    search.add_argument("--job-type", help="Comma-separated: full-time,part-time,contract,temporary,internship")
    search.add_argument("--easy-apply", action="store_true", help="Limit to Easy Apply postings")
    search.add_argument("--geo-id", help="LinkedIn location geoId")
    search.add_argument("--pages", type=int, default=1, help="Page count, 1–5 (default: 1)")
    search.add_argument("--limit", type=int, help="Maximum results across pages")
    search.add_argument("--format", choices=("json", "md"), default="json", metavar="json|md")
    detail = commands.add_parser("detail", help="Fetch one public posting's detail")
    detail.add_argument("job_id_or_url", metavar="JOB_ID|URL|URN")
    detail.add_argument("--format", choices=("json", "md"), default="json", metavar="json|md")
    args = parser.parse_args()
    warn_tos()
    try:
        if args.command == "search":
            if args.pages < 1 or args.pages > MAX_PAGES:
                parser.error("--pages must be between 1 and {}".format(MAX_PAGES))
            if args.minutes is not None and args.minutes < 1:
                parser.error("--minutes must be at least 1")
            if args.limit is not None and args.limit < 1:
                parser.error("--limit must be at least 1")
            if args.experience:
                _mapped_values(args.experience, EXPERIENCE_FLAGS, "experience")
            if args.job_type:
                _mapped_values(args.job_type, JOB_TYPE_FLAGS, "job type")
            results = search_jobs(
                args.location, args.query, args.days, args.minutes, args.remote,
                args.experience, args.job_type, args.easy_apply, args.geo_id,
                args.pages, args.limit,
            )
            payload = {"meta": {"count": len(results), "pages": args.pages}, "results": results}
            output = json_text(payload) if args.format == "json" else _search_markdown(results)
        else:
            job = fetch_job_detail(args.job_id_or_url)
            output = json_text(job) if args.format == "json" else _detail_markdown(job)
        write_output(output)
        return 0
    except (LinkedInRequestError, ValueError, OSError) as error:
        print(json_text({"error": str(error), "code": "LINKEDIN_FAILED"}, indent=None), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
