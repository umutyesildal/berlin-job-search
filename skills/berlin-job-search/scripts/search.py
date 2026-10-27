from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

from _common import (
    USER_AGENT,
    ba_location_name,
    canonical_url,
    canonical_location_name,
    city_alias,
    city_location_matches,
    csv_text,
    json_text,
    linkedin_location_and_geo_id,
    markdown_table,
    normalize_identity as normalized_key,
    plain,
    protect_if_inside_data_dir,
    request_bytes,
    request_json,
    salary_text,
    source_date as iso_date,
    warn_data_dir_protection,
    write_output,
)
from analyze_jd import analyze, employment_type_from_title

DEFAULT_COMPANIES = Path(__file__).resolve().parent.parent / "assets" / "companies.json"
DEFAULT_SOURCES = ("dailyberlinjobs", "ba", "arbeitnow", "ats", "linkedin")
TITLE_MATCH_STOP_WORDS = frozenset({
    "senior", "junior", "sr", "jr", "lead", "head", "principal", "werkstudent",
    "werkstudentin", "working", "student", "intern", "internship", "praktikant",
    "praktikantin", "praktikum", "trainee", "the", "of", "and", "und", "for",
    "fur", "mit", "with", "gn", "mwd", "mfd", "all", "genders",
})
TITLE_MATCH_FOLD = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"})


def merge_integrity(*results):
    findings = [result for result in results if isinstance(result, dict)]
    return {"ok": all(result.get("ok", True) for result in findings),
            "issues": [issue for result in findings for issue in result.get("issues", [])]}


def make_job(
    source: str, title: Any, company: Any, location: Any, url: Any, posted: Any = None,
    remote: Any = None, employment_type: Any = None, salary: Any = None,
    description: Any = None, include_description: bool = False, job_id: Any = None,
) -> dict[str, Any]:
    title_text = plain(title)
    raw_description = str(description or "")
    text = plain(raw_description)
    analysis = analyze(raw_description) if raw_description else {}
    record = {
        "id": str(job_id or url or "{}|{}|{}".format(source, company, title)),
        "source": source,
        "title": title_text,
        "company": plain(company),
        "location": plain(location),
        "remote": remote if isinstance(remote, bool) else None,
        "url": str(url or ""),
        "posted": iso_date(posted),
        "employment_type": employment_type_from_title(title_text) or plain(employment_type) or None,
        "salary": salary_text(salary),
        "ad_language": analysis.get("ad_language", "unknown"),
        "german_required": analysis.get("german_required", "unknown"),
        "integrity": analysis.get("integrity", {"ok": True, "issues": []}),
    }
    if include_description:
        record["description"] = text[:4000]
    return record


def parse_ba(payload: dict[str, Any], include_description: bool = False) -> list[dict[str, Any]]:
    jobs = []
    for item in payload.get("ergebnisliste", []):
        locations = item.get("stellenlokationen") or []
        addresses = []
        for location in locations:
            address = location.get("adresse") or {}
            bits = [address.get("ort"), address.get("plz")]
            address_text = ", ".join(str(bit) for bit in bits if bit)
            if address_text:
                addresses.append(address_text)
        refnr = item.get("referenznummer", "")
        detail_url = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{}".format(urllib.parse.quote(str(refnr), safe="-"))
        remote_value = item.get("homeofficemoeglich")
        if isinstance(remote_value, str):
            remote_value = remote_value.casefold() in {"true", "ja", "yes", "y"}
        work_types = []
        if item.get("arbeitszeitVollzeit"):
            work_types.append("Vollzeit")
        if any(item.get(key) for key in (
            "arbeitszeitTeilzeitAbend", "arbeitszeitTeilzeitNachmittag",
            "arbeitszeitTeilzeitVormittag", "arbeitszeitTeilzeitFlexibel",
        )):
            work_types.append("Teilzeit")
        contract = str(item.get("vertragsdauer", "")).casefold()
        if contract and contract not in {"keine_angabe", "keine angabe", "unbekannt"}:
            work_types.append(contract)
        jobs.append(make_job(
            "ba", item.get("stellenangebotsTitel"), item.get("firma"), " / ".join(addresses),
            detail_url, item.get("datumErsteVeroeffentlichung"), remote_value if isinstance(remote_value, bool) else None,
            employment_type=", ".join(work_types) or None,
            salary=item.get("verguetungsangabe"), include_description=include_description, job_id=refnr,
        ))
        if item.get("externeURL"):
            jobs[-1]["external_url"] = item["externeURL"]
    return jobs


def parse_arbeitnow(payload: dict[str, Any], include_description: bool = False) -> list[dict[str, Any]]:
    jobs = []
    for item in payload.get("data", []):
        jobs.append(make_job(
            "arbeitnow", item.get("title"), item.get("company_name"), item.get("location"), item.get("url"),
            item.get("created_at"), bool(item.get("remote")) if item.get("remote") is not None else None,
            ", ".join(str(tag) for tag in item.get("job_types", []) if tag),
            description=item.get("description"), include_description=include_description,
            job_id=item.get("slug"),
        ))
    return jobs


def parse_greenhouse(payload: dict[str, Any], company: dict[str, Any], include_description: bool = False) -> list[dict[str, Any]]:
    jobs = []
    for item in payload.get("jobs", []):
        location = item.get("location") or {}
        location_text = location.get("name", "") if isinstance(location, dict) else str(location)
        offices = [office.get("name", "") for office in item.get("offices") or [] if office.get("name")]
        employment = ""
        for field in item.get("metadata") or []:
            if str(field.get("name", "")).casefold() in {"job type", "employment type"}:
                value = field.get("value")
                employment = ", ".join(str(part) for part in value) if isinstance(value, list) else str(value or "")
                break
        jobs.append(make_job(
            "ats", item.get("title"), company["name"], " / ".join(offices) or location_text,
            item.get("absolute_url"), item.get("first_published") or item.get("updated_at"),
            "remote" in (location_text + " " + " ".join(offices)).casefold() if location_text or offices else None,
            employment_type=employment or None, description=item.get("content"),
            include_description=include_description, job_id=item.get("id"),
        ))
    return jobs


def parse_lever(payload: list[dict[str, Any]], company: dict[str, Any], include_description: bool = False) -> list[dict[str, Any]]:
    jobs = []
    for item in payload:
        categories = item.get("categories") or {}
        location = categories.get("location", "")
        if categories.get("allLocations"):
            location = " / ".join(categories["allLocations"])
        remote_field = item.get("workplaceType")
        remote = True if str(remote_field).casefold() == "remote" else False if remote_field else ("remote" in location.casefold() if location else None)
        jobs.append(make_job(
            "ats", item.get("text"), company["name"], location, item.get("hostedUrl"),
            item.get("createdAt"), remote, categories.get("commitment"),
            description=item.get("descriptionPlain") or item.get("openingPlain") or item.get("description"),
            include_description=include_description, job_id=item.get("id"),
        ))
    return jobs


def parse_ashby(payload: dict[str, Any], company: dict[str, Any], include_description: bool = False) -> list[dict[str, Any]]:
    jobs = []
    for item in payload.get("jobs", []):
        locations = [item.get("location", "")]
        for secondary in item.get("secondaryLocations") or []:
            if isinstance(secondary, dict):
                locations.append(secondary.get("location") or secondary.get("name") or "")
            else:
                locations.append(str(secondary))
        location_text = " / ".join(dict.fromkeys(value for value in locations if value))
        jobs.append(make_job(
            "ats", item.get("title"), company["name"], location_text,
            item.get("jobUrl") or item.get("applyUrl"), item.get("publishedAt"),
            bool(item.get("isRemote")) if item.get("isRemote") is not None else None,
            item.get("employmentType"), item.get("compensation"),
            item.get("descriptionPlain") or item.get("descriptionHtml"), include_description,
            item.get("id"),
        ))
    return jobs


def parse_personio_xml(
    body: bytes,
    company: dict[str, Any],
    include_description: bool = False,
) -> list[dict[str, Any]]:
    root = ET.fromstring(body)
    jobs = []
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1].casefold() not in {"position", "job"}:
            continue
        fields = {child.tag.rsplit("}", 1)[-1].casefold(): " ".join("".join(child.itertext()).split()) for child in item}
        title = fields.get("name") or fields.get("title")
        if not title:
            continue
        location = fields.get("office") or fields.get("location", "")
        description_parts = []
        for child in item.iter():
            tag = child.tag.rsplit("}", 1)[-1].casefold()
            if tag in {"value", "description"} and child.text:
                description_parts.append(child.text.strip())
        description = fields.get("jobdescription") or fields.get("description", "") or " ".join(description_parts)
        url = fields.get("url") or fields.get("link") or ""
        if url and not url.startswith("http"):
            url = company["career_page"].rstrip("/") + "/" + url.lstrip("/")
        if not url and fields.get("id"):
            url = company["career_page"].rstrip("/") + "/job/" + fields["id"]
        jobs.append(make_job(
            "ats", title, company["name"], location, url, fields.get("createdat"),
            "remote" in location.casefold() if location else None,
            fields.get("employmenttype"), fields.get("salary"), description, include_description,
            fields.get("id"),
        ))
    return jobs


def parse_personio_json(payload: dict[str, Any], company: dict[str, Any], include_description: bool = False) -> list[dict[str, Any]]:
    rows = payload.get("positionResults") or payload.get("jobs") or payload.get("data") or []
    jobs = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        locations = item.get("offices") or []
        location = ", ".join(str(place.get("name", "")) for place in locations if place.get("name"))
        url = item.get("url") or item.get("jobUrl")
        if url and not str(url).startswith("http"):
            url = company["career_page"].rstrip("/") + "/" + str(url).lstrip("/")
        jobs.append(make_job(
            "ats", item.get("name") or item.get("title"), company["name"], location or item.get("office", ""),
            url, item.get("created_at") or item.get("publishedAt"),
            "remote" in str(location).casefold() if location else None,
            item.get("employment_type"), item.get("salary"), item.get("description"),
            include_description, item.get("id"),
        ))
    return jobs


def _city_country_locations(locations: list[dict[str, Any]]) -> str:
    return " / ".join(
        ", ".join(str(value) for value in (place.get("city"), place.get("country")) if value)
        for place in locations
    )


def parse_smartrecruiters(payload: dict[str, Any], company: dict[str, Any], include_description: bool = False) -> list[dict[str, Any]]:
    jobs = []
    for item in payload.get("content", []):
        location = item.get("location") or {}
        loc = ", ".join(str(location[key]) for key in ("city", "region", "country") if location.get(key))
        employment = item.get("typeOfEmployment") or {}
        job_ad = item.get("jobAd") or {}
        sections = job_ad.get("sections") or {}
        description = sections.get("jobDescription") or {}
        jobs.append(make_job(
            "ats", item.get("name"), company["name"], loc,
            item.get("applyUrl") or "https://careers.smartrecruiters.com/{}/{}".format(company["slug"], item.get("id", "")),
            item.get("releasedDate"), None,
            employment.get("label") if isinstance(employment, dict) else str(employment),
            description=description.get("text") if isinstance(description, dict) else str(description),
            include_description=include_description, job_id=item.get("id"),
        ))
    return jobs


def parse_recruitee(payload: dict[str, Any], company: dict[str, Any], include_description: bool = False) -> list[dict[str, Any]]:
    jobs = []
    for item in payload.get("offers", []):
        locations = item.get("locations") or []
        location = _city_country_locations(locations)
        location = location or item.get("location") or item.get("city", "")
        jobs.append(make_job(
            "ats", item.get("title"), company["name"], location, item.get("careers_url") or item.get("url"),
            item.get("published_at"), bool(item.get("remote")) if item.get("remote") is not None else None,
            item.get("employment_type_code"), item.get("salary"),
            item.get("description"), include_description, item.get("id"),
        ))
    return jobs


def parse_workable(payload: dict[str, Any], company: dict[str, Any], include_description: bool = False) -> list[dict[str, Any]]:
    jobs = []
    for item in payload.get("jobs", []):
        locations = item.get("locations") or []
        location = _city_country_locations(locations)
        jobs.append(make_job(
            "ats", item.get("title"), company["name"], location or item.get("city"), item.get("url"),
            item.get("published_on"), bool(item.get("telecommuting")) if item.get("telecommuting") is not None else None,
            item.get("employment_type"), item.get("salary"), item.get("description"),
            include_description, item.get("shortcode"),
        ))
    return jobs


def parse_join(payload: dict[str, Any], company: dict[str, Any], include_description: bool = False) -> list[dict[str, Any]]:
    jobs = []
    for item in payload.get("items", []):
        city = (item.get("city") or {}).get("cityName", "")
        country = (item.get("country") or {}).get("name", "")
        location = ", ".join(part for part in (city, country) if part)
        workplace = str(item.get("workplaceType", "")).casefold()
        remote = True if workplace == "remote" else False if workplace else None
        jobs.append(make_job(
            "ats", item.get("title"), company["name"], location,
            item.get("url") or "https://join.com/companies/{}/jobs/{}".format(company["slug"], item.get("idParam", "")),
            item.get("createdAt"), remote, (item.get("employmentType") or {}).get("name"),
            description=item.get("description"), include_description=include_description, job_id=item.get("id"),
        ))
    return jobs


ATS_JSON_BOARDS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", parse_greenhouse),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true", parse_ashby),
    "smartrecruiters": ("https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100", parse_smartrecruiters),
    "recruitee": ("https://{slug}.recruitee.com/api/offers", parse_recruitee),
    "workable": ("https://apply.workable.com/api/v1/widget/accounts/{slug}", parse_workable),
    "join": ("https://join.com/api/public/companies/{slug}/jobs?locale=en-us&page=1&pageSize=25", parse_join),
}


def ats_jobs(company: dict[str, Any], include_description: bool = False) -> list[dict[str, Any]]:
    ats = str(company.get("ats", "")).casefold()
    slug = str(company.get("slug", ""))
    if ats == "lever":
        data = []
        for skip in range(0, 10000, 100):
            page_url = "https://api.lever.co/v0/postings/{}?{}".format(
                urllib.parse.quote(slug),
                urllib.parse.urlencode({"mode": "json", "limit": 100, "skip": skip}),
            )
            page = request_json(page_url)
            if not isinstance(page, list):
                raise RuntimeError("unexpected Lever response format")
            data.extend(page)
            if len(page) < 100:
                break
        return parse_lever(data, company, include_description)
    if ats == "personio":
        url = "https://{}.jobs.personio.de/xml?language=en".format(urllib.parse.quote(slug))
        status, body = request_bytes(url, headers={"Accept": "application/xml"})
        if 200 <= status < 300:
            return parse_personio_xml(body, company, include_description)
        payload = request_json("https://{}.jobs.personio.de/search.json".format(urllib.parse.quote(slug)))
        if isinstance(payload, list):
            payload = {"positionResults": payload}
        return parse_personio_json(payload, company, include_description)
    if ats in ATS_JSON_BOARDS:
        endpoint, parser = ATS_JSON_BOARDS[ats]
        data = request_json(endpoint.format(slug=urllib.parse.quote(slug)))
        return parser(data, company, include_description)
    raise ValueError("Unsupported ATS: {}".format(ats))


def dedup_key(job: dict[str, Any]) -> tuple[str, str]:
    url = canonical_url(job.get("url", ""))
    city = re.split(r"[,/|]", job.get("location") or "")[0]
    text_key = "|".join(normalized_key(str(job.get(field) or "")) for field in ("company", "title")) + "|" + normalized_key(city)
    return url, text_key


def deduplicate_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parents = list(range(len(jobs)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    key_owner: dict[tuple[str, str], int] = {}
    for index, job in enumerate(jobs):
        url_key, identity_key = dedup_key(job)
        keys = ([("url", url_key)] if url_key else []) + [("identity", identity_key)]
        for key in keys:
            owner = key_owner.get(key)
            if owner is None:
                key_owner[key] = index
            else:
                union(index, owner)

    groups: dict[int, list[int]] = {}
    for index in range(len(jobs)):
        groups.setdefault(find(index), []).append(index)
    ordered_groups = sorted(groups.values(), key=lambda indexes: indexes[0])
    unique = []
    for indexes in ordered_groups:
        winner = max(
            indexes,
            key=lambda index: (
                bool(jobs[index].get("description")),
                jobs[index].get("source") == "dailyberlinjobs",
                -index,
            ),
        )
        selected = dict(jobs[winner])
        findings = [job.get("integrity") for job in (jobs[i] for i in indexes)]
        flagged = [result for result in findings if result and not result.get("ok", True)]
        if flagged:
            selected["integrity"] = {"ok": False, "issues": [issue for result in flagged for issue in result.get("issues", [])]}
        unique.append(selected)
    return unique


def mark_seen_jobs(
    jobs: list[dict[str, Any]],
    seen_file: Optional[str] = None,
    only_new: bool = False,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    if not seen_file:
        for job in jobs:
            job["new"] = True
        return jobs[:limit] if limit is not None else jobs
    path = Path(seen_file)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = payload.get("seen") if isinstance(payload, dict) else None
        if not isinstance(entries, dict):
            raise ValueError("seen file must contain an object with a 'seen' map")
    else:
        entries = {}
    today = dt.date.today().isoformat()
    pending = []
    for job in jobs:
        url_key, identity_key = dedup_key(job)
        keys = []
        if url_key:
            keys.append("url:" + url_key)
        if any(job.get(field) for field in ("company", "title", "location")):
            keys.append("identity:" + identity_key)
        previous_dates = [entries[key] for key in keys if isinstance(entries.get(key), str)]
        job["new"] = not bool(previous_dates)
        first_seen = min(previous_dates) if previous_dates else today
        pending.append((job, keys, first_seen))
    selected = [item for item in pending if not only_new or item[0]["new"]]
    if limit is not None:
        selected = selected[:limit]
    for _, keys, first_seen in selected:
        for key in keys:
            entries.setdefault(key, first_seen)
    protection = protect_if_inside_data_dir(path)
    if protection is not None:
        warn_data_dir_protection(protection)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_text({"version": 1, "seen": entries}) + "\n", encoding="utf-8")
    return [job for job, _, _ in selected]


def filter_jobs(jobs: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    cutoff = dt.date.today() - dt.timedelta(days=args.days) if args.days else None
    query_words = [word.casefold() for word in re.findall(r"\w+", args.what)]
    city = args.where or ""
    filtered = []
    for job in jobs:
        if args.remote and job.get("remote") is not True:
            continue
        if args.english_only and job.get("german_required") == "required":
            continue
        if cutoff and job.get("posted") and job.get("source") != "dailyberlinjobs":
            try:
                if dt.date.fromisoformat(job["posted"]) < cutoff:
                    continue
            except ValueError:
                pass
        if city:
            location = (job.get("location") or "").casefold()
            broad_remote = args.remote and any(token in location for token in ("germany", "remote", "deutschland"))
            if not city_location_matches(city, job.get("location")) and not broad_remote:
                continue
        if not getattr(args, "title_match", False):
            title_terms = re.findall(r"\w+", (job.get("title") or "").casefold())
            if job.get("source") == "arbeitnow":
                title_terms += re.findall(r"\w+", (job.get("_tags") or "").casefold())
            if job.get("source") == "dailyberlinjobs":
                title_terms += re.findall(r"\w+", " ".join(job.get("tech_stack") or []).casefold())
            if job.get("source") in {"ats", "dailyberlinjobs"} and any(
                word not in " ".join(title_terms) for word in query_words
            ):
                continue
            if job.get("source") == "arbeitnow" and any(
                word not in " ".join(title_terms) for word in query_words
            ):
                continue
        filtered.append(job)
    return filtered


def title_words(value: Any) -> list[str]:
    normalized = str(value or "").casefold().translate(TITLE_MATCH_FOLD)
    normalized = re.sub(r"(?<!\w)m\s*/\s*w\s*/\s*d(?!\w)", " mwd ", normalized)
    normalized = re.sub(r"(?<!\w)m\s*/\s*f\s*/\s*d(?!\w)", " mfd ", normalized)
    words = []
    current = []
    for char in normalized:
        if char.isalnum():
            current.append(char)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words


def title_match_terms(query: str) -> tuple[list[str], bool]:
    words = title_words(query)
    terms = [word for word in words if word not in TITLE_MATCH_STOP_WORDS]
    if terms:
        return terms, False
    phrase = " ".join(words)
    return ([phrase] if phrase else []), True


TITLE_GENDER_SUFFIXES = frozenset({"in", "innen"})


def _phrase_at(words: list[str], phrase: list[str], start: int) -> bool:
    position = start
    for offset, term in enumerate(phrase):
        last = offset == len(phrase) - 1
        for candidate in range(position, min(len(words), position + (1 if offset == 0 else 2))):
            word = words[candidate]
            if word.startswith(term) if last else word == term:
                position = candidate + 1
                break
        else:
            return False
    return True


def title_matches(query: str, title: Any) -> bool:
    terms, phrase_fallback = title_match_terms(query)
    words = [word for word in title_words(title) if word not in TITLE_GENDER_SUFFIXES]
    if not terms or not words:
        return False
    phrase = terms[0].split() if phrase_fallback else terms
    if any(_phrase_at(words, phrase, index) for index in range(len(words))):
        return True
    if len(phrase) > 1:
        joined = "".join(phrase)
        return any(word.startswith(joined) for word in words)
    return False


def freehire_salary(enrichment: dict[str, Any]) -> Optional[str]:
    minimum = enrichment.get("salary_min")
    maximum = enrichment.get("salary_max")
    if minimum is None and maximum is None:
        return None
    currency = enrichment.get("salary_currency")
    period = enrichment.get("salary_period")
    if minimum is not None and maximum is not None:
        value = str(minimum) if minimum == maximum else "{}–{}".format(minimum, maximum)
    else:
        value = str(minimum if minimum is not None else maximum)
    return "{}{}{}".format("{} ".format(currency) if currency else "", value, "/{}".format(period) if period else "")


def parse_freehire(payload: dict[str, Any], include_description: bool = False) -> list[dict[str, Any]]:
    jobs = []
    for item in payload.get("data") or []:
        enrichment = item.get("enrichment") or {}
        description = item.get("description") or None
        mode = str(item.get("work_mode") or "").casefold()
        remote = True if mode == "remote" else False if mode in {"hybrid", "onsite", "on-site"} else None
        jobs.append(make_job(
            "freehire", item.get("title"), item.get("company"), item.get("location"),
            item.get("url"), item.get("posted_at") or item.get("created_at"), remote,
            enrichment.get("employment_type"), freehire_salary(enrichment),
            description, include_description=include_description,
            job_id=item.get("public_slug"),
        ))
    return jobs


def dailyberlinjobs_query_term(query: str) -> str:
    words = re.findall(r"\w+", query.casefold())
    if not words:
        return query.strip()
    return max(words, key=len)


def validate_dbj_filter_values(value: Optional[str], allowed: list[str], option: str) -> list[str]:
    requested = [item.strip() for item in (value or "").split(",") if item.strip()]
    allowed_values = {item.casefold(): item for item in allowed}
    invalid = [item for item in requested if item.casefold() not in allowed_values]
    if invalid:
        allowed_text = ", ".join(allowed) if allowed else "(none returned by /api/v1/status)"
        raise ValueError(
            "{} value(s) not in status.filters: {}. Allowed values: {}".format(
                option, ", ".join(invalid), allowed_text,
            )
        )
    return list(dict.fromkeys(allowed_values[item.casefold()] for item in requested))


def is_daily_berlin_jobs_location(where: Optional[str]) -> bool:
    if not where:
        return True
    alias = city_alias(where)
    return bool(alias and alias["key"] == "berlin")


def parse_dailyberlinjobs(payload: dict[str, Any], include_description: bool = False) -> list[dict[str, Any]]:
    jobs = []
    for item in payload.get("jobs") or []:
        via = str(item.get("source") or "").casefold()
        work_mode = str(item.get("work_mode") or "")
        normalized_mode = work_mode.casefold()
        remote = True if normalized_mode == "remote" else False if normalized_mode in {"on-site", "onsite"} else None
        description = item.get("description")
        job = make_job(
            "dailyberlinjobs", item.get("title"), item.get("company"), item.get("location"),
            item.get("url"), item.get("posted_date"), remote,
            description=description, include_description=include_description,
            job_id=item.get("id"),
        )
        job.update({
            "via": via,
            "detail_url": item.get("detail_url"),
            "work_mode": work_mode or None,
            "role": item.get("role"),
            "level": item.get("level"),
            "tech_stack": list(item.get("tech_stack") or []),
        })
        if include_description and not description:
            job["description"] = None
        jobs.append(job)
    return jobs


def fetch_dailyberlinjobs(
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    if getattr(args, "dbj_skipped", False):
        return [], [], {
            "skipped": "Berlin-only source",
            "updated_at": None,
            "stale": None,
        }
    status_error = getattr(args, "dbj_status_error", None)
    status = getattr(args, "dbj_status_payload", None)
    if status_error or not isinstance(status, dict):
        message = status_error or "Daily Berlin Jobs status response unavailable"
        return [], ["status: {}".format(message)], {"updated_at": None, "stale": None}

    base_url = os.environ.get(
        "GJS_DBJ_URL", "https://daily-berlin-jobs.vercel.app",
    ).rstrip("/")
    metadata = {"updated_at": status.get("updated_at"), "stale": status.get("stale")}
    query = dailyberlinjobs_query_term(args.what)
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)).isoformat().replace("+00:00", "Z")
    params: dict[str, str] = {"since": since, "q": query, "limit": "500"}
    role_values = getattr(args, "dbj_role_values", [])
    level_values = getattr(args, "dbj_level_values", [])
    if role_values:
        params["role"] = ",".join(role_values)
    if level_values:
        params["level"] = ",".join(level_values)
    if args.remote:
        params["work_mode"] = "Remote"

    jobs: list[dict[str, Any]] = []
    errors: list[str] = []
    detail_requests = 0
    cursor: Optional[str] = None
    for page in range(args.max_pages):
        page_params = dict(params)
        if cursor:
            page_params["cursor"] = cursor
        url = base_url + "/api/v1/jobs?" + urllib.parse.urlencode(page_params)
        try:
            payload = request_json(url)
            if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
                raise RuntimeError("unexpected Daily Berlin Jobs response format")
        except Exception as error:
            errors.append("list page {}: {}".format(page + 1, error))
            break

        page_jobs = parse_dailyberlinjobs(payload, args.with_description)
        for item, job in zip(payload["jobs"], page_jobs):
            if args.with_description and job.get("via") == "ats" and detail_requests < 10:
                detail_requests += 1
                identifier = str(item.get("id") or "")
                detail_url = (base_url + "/api/v1/jobs/" + identifier
                              if re.fullmatch(r"[0-9]+", identifier) else None)
                if detail_url:
                    try:
                        detail = request_json(str(detail_url))
                        raw_description = detail.get("description")
                        if isinstance(raw_description, str) and raw_description.strip():
                            analysis = analyze(raw_description)
                            job["description"] = plain(raw_description)[:4000]
                            job["integrity"] = merge_integrity(job.get("integrity"), analysis.get("integrity"))
                            job["ad_language"] = analysis["ad_language"]
                            job["german_required"] = analysis["german_required"]
                            job["employment_type"] = plain(detail.get("employment_type")) or None
                        else:
                            job["description"] = None
                    except Exception as error:
                        errors.append("detail {}: {}".format(item.get("id"), error))
                        job["description"] = None
            jobs.append(job)
        cursor = payload.get("next_cursor")
        if not cursor:
            break
    return jobs, errors, metadata


def fetch_freehire(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    base_url = os.environ.get("FREEHIRE_API_URL", "https://freehire.me").rstrip("/")
    include_description = args.with_description
    endpoint = "/api/v1/agent/jobs/search" if include_description else "/api/v1/jobs/search"
    page_size = min(args.limit, 100)
    city = canonical_location_name(args.where)
    jobs = []
    errors = []
    for page in range(args.max_pages):
        params = {
            "q": args.what,
            "countries": "de",
            "limit": str(page_size),
            "offset": str(page * page_size),
        }
        if args.days > 0:
            params["posted_within_days"] = str(args.days)
        if city:
            params["cities"] = city
        if args.remote:
            params["work_mode"] = "remote"
        if include_description:
            params["description_format"] = "text"
        url = base_url + endpoint + "?" + urllib.parse.urlencode(params)
        try:
            payload = request_json(url)
            ignored = payload.get("meta", {}).get("ignored_params") or []
            expected_filters = {"q", "countries", "posted_within_days", "cities", "work_mode"}
            ignored_names = {
                str(value.get("param")) if isinstance(value, dict) else str(value)
                for value in ignored
            }
            ignored_filters = expected_filters.intersection(ignored_names)
            if ignored_filters:
                errors.append("page {} ignored filter(s): {}".format(
                    page + 1, ", ".join(sorted(ignored_filters)),
                ))
                break
            rows = payload.get("data") or []
            page_jobs = parse_freehire(payload, include_description)
            for job in page_jobs:
                remote = job.get("remote")
                if city:
                    location = (job.get("location") or "").casefold()
                    broad_remote = args.remote and remote is True and any(
                        token in location for token in ("germany", "deutschland", "remote")
                    )
                    if not city_location_matches(args.where, job.get("location")) and not broad_remote:
                        continue
                jobs.append(job)
        except Exception as error:
            errors.append("page {}: {}".format(page + 1, error))
            break
        total = (payload.get("meta") or {}).get("total")
        if not rows or (isinstance(total, int) and (page + 1) * page_size >= total):
            break
    return jobs, errors


def fetch_ats(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    companies = json.loads(Path(args.companies).read_text(encoding="utf-8"))
    errors: list[str] = []
    jobs: list[dict[str, Any]] = []
    if not companies:
        return jobs, errors
    worker_count = min(4, len(companies))

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
        pending = {
            pool.submit(ats_jobs, company, args.with_description): company
            for company in companies[:worker_count]
        }
        remaining = iter(companies[worker_count:])
        while pending:
            done, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                company = pending.pop(future)
                try:
                    jobs.extend(future.result())
                except Exception as error:
                    errors.append("{}: {}".format(company.get("name", company.get("slug", "company")), error))
                next_company = next(remaining, None)
                if next_company is not None:
                    pending[pool.submit(ats_jobs, next_company, args.with_description)] = next_company
    return jobs, errors


def linkedin_remote_value(location: str, remote_filter: bool) -> Optional[bool]:
    if remote_filter or re.search(r"\bremote\b", location, re.I):
        return True
    if re.search(r"\b(?:hybrid|on[- ]?site)\b", location, re.I):
        return False
    return None


def fetch_linkedin(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    from linkedin import (
        LinkedInRequestError,
        fetch_job_detail as fetch_linkedin_detail,
        search_jobs_with_errors as search_linkedin_jobs,
    )

    location, geo_id = linkedin_location_and_geo_id(args.where or "Germany")
    cards, errors = search_linkedin_jobs(
        location=location,
        query=args.what,
        days=args.days if args.days > 0 else None,
        remote="remote" if args.remote else None,
        pages=args.linkedin_pages,
        limit=args.limit,
        geo_id=geo_id,
    )
    jobs = []
    detail_failed = False
    for index, item in enumerate(cards):
        location = item.get("location") or ""
        remote = linkedin_remote_value(location, args.remote)
        description = None
        employment_type = None
        salary = None
        include_description = args.with_description and index < 10
        if include_description:
            try:
                detail = fetch_linkedin_detail(item["id"])
                description = detail.get("description")
                employment_type = detail.get("employment_type")
                analysis = detail.get("analysis") or {}
                salary = (analysis.get("salary") or {}).get("raw")
            except LinkedInRequestError as error:
                errors.append("job {}: {}".format(item.get("id"), error))
                include_description = False
                detail_failed = True
        job = make_job(
            "linkedin", item.get("title"), item.get("company"), location, item.get("url"),
            item.get("date"), remote, employment_type, salary, description,
            include_description=include_description, job_id=item.get("id"),
        )
        if include_description:
            job["integrity"] = merge_integrity(job.get("integrity"), (detail.get("analysis") or {}).get("integrity"))
        if args.with_description and not include_description:
            job["description"] = None
        jobs.append(job)
        if detail_failed:
            for remaining in cards[index + 1:]:
                remaining_location = remaining.get("location") or ""
                remaining_remote = linkedin_remote_value(remaining_location, args.remote)
                remainder = make_job(
                    "linkedin", remaining.get("title"), remaining.get("company"),
                    remaining_location, remaining.get("url"), remaining.get("date"),
                    remaining_remote, job_id=remaining.get("id"),
                )
                if args.with_description:
                    remainder["description"] = None
                jobs.append(remainder)
            break
    return jobs, errors


def fetch_ba_source(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    jobs: list[dict[str, Any]] = []
    errors: list[str] = []
    for page in range(1, min(args.max_pages, 100) + 1):
        params = {
            "was": args.what,
            "veroeffentlichtseit": str(args.days),
            "size": "100",
            "page": str(page),
        }
        if args.where:
            params.update({"wo": ba_location_name(args.where), "umkreis": str(args.radius)})
        url = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs?" + urllib.parse.urlencode(params)
        try:
            payload = request_json(url, headers={"X-API-Key": "jobboerse-jobsuche"})
        except Exception as error:
            errors.append("page {}: {}".format(page, error))
            break
        current = parse_ba(payload, args.with_description)
        jobs.extend(current)
        if not current or len(jobs) >= int(payload.get("maxErgebnisse", 0) or 0):
            break
    return jobs, errors


def fetch_arbeitnow_source(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    jobs: list[dict[str, Any]] = []
    errors: list[str] = []
    for page in range(1, args.max_pages + 1):
        try:
            payload = request_json("https://www.arbeitnow.com/api/job-board-api?page={}".format(page))
        except Exception as error:
            errors.append("page {}: {}".format(page, error))
            break
        page_jobs = parse_arbeitnow(payload, args.with_description)
        for job, item in zip(page_jobs, payload.get("data", [])):
            job["_tags"] = " ".join(str(tag) for tag in item.get("tags", []))
        jobs.extend(page_jobs)
        if not payload.get("data"):
            break
    return jobs, errors


SOURCE_FETCHERS = {
    "dailyberlinjobs": fetch_dailyberlinjobs,
    "ba": fetch_ba_source,
    "arbeitnow": fetch_arbeitnow_source,
    "ats": fetch_ats,
    "linkedin": fetch_linkedin,
    "freehire": fetch_freehire,
}


def source_status(
    source: str,
    count: int,
    errors: list[str],
    produced_count: Optional[int] = None,
) -> dict[str, Any]:
    received = count if produced_count is None else produced_count
    failed = received == 0 and bool(errors)
    return {
        "ok": not failed,
        "count": count,
        "error": "; ".join(errors) if failed else None,
        "partial_errors": list(errors),
    }


def source_status_line(source: str, status: dict[str, Any]) -> str:
    count = status["count"]
    label = "ok" if status["ok"] else "error"
    line = "{}: {}, {} job{}".format(source, label, count, "" if count == 1 else "s")
    errors = status.get("partial_errors") or []
    if errors:
        if source == "ats":
            error_label = "company error" if len(errors) == 1 else "company errors"
        elif source == "freehire":
            error_label = "query error" if len(errors) == 1 else "query errors"
        else:
            error_label = "partial error" if len(errors) == 1 else "partial errors"
        line += " ({} {})".format(len(errors), error_label)
    if status.get("error"):
        line += " — " + status["error"]
    return line


def format_markdown(jobs: list[dict[str, Any]], statuses: dict[str, dict[str, Any]]) -> str:
    rows = []
    for job in jobs:
        rows.append([
            job.get("title"), job.get("company"), job.get("location"), job.get("posted") or "",
            "yes" if job.get("remote") is True else "no" if job.get("remote") is False else "unknown",
            job.get("german_required"), job.get("source"), job.get("url"),
            "yes" if job.get("new") else "no",
        ])
    table = markdown_table(
        ["Title", "Company", "Location", "Posted", "Remote", "German", "Source", "URL", "New"],
        rows,
    )
    statuses_text = "\n".join("- " + source_status_line(source, status) for source, status in statuses.items())
    warnings = ["Integrity warning: posting {} requires review.".format(index + 1)
                for index, job in enumerate(jobs) if not job.get("integrity", {}).get("ok", True)]
    return table + "\n\n" + statuses_text + ("\n\n" + "\n".join(warnings) if warnings else "")


def run_search(params: dict[str, Any]) -> dict[str, Any]:
    values = {
        "what": "",
        "where": None,
        "radius": 25,
        "days": 7,
        "sources": ",".join(DEFAULT_SOURCES),
        "companies": str(DEFAULT_COMPANIES),
        "remote": False,
        "english_only": False,
        "limit": 100,
        "max_pages": 3,
        "linkedin_pages": 1,
        "dbj_role": None,
        "dbj_level": None,
        "seen_file": None,
        "only_new": False,
        "format": "json",
        "out": None,
        "with_description": False,
        "title_match": False,
    }
    values.update(params)
    args = argparse.Namespace(**values)

    if not str(args.what or "").strip():
        raise ValueError("--what must not be empty")
    if args.max_pages < 1 or args.limit < 1 or args.days < 0 or args.radius < 0:
        raise ValueError("--max-pages, --limit, and --radius must be positive; --days cannot be negative")
    if args.linkedin_pages < 1 or args.linkedin_pages > 5:
        raise ValueError("--linkedin-pages must be between 1 and 5")
    if args.only_new and not args.seen_file:
        raise ValueError("--only-new requires --seen-file")

    requested = [source.strip().casefold() for source in str(args.sources).split(",") if source.strip()]
    unknown = sorted(set(requested) - set(SOURCE_FETCHERS))
    if unknown:
        raise ValueError("unsupported source(s): {}".format(", ".join(unknown)))
    if (args.dbj_role or args.dbj_level) and "dailyberlinjobs" not in requested:
        raise ValueError("--dbj-role and --dbj-level require --sources dailyberlinjobs")

    args.dbj_skipped = False
    args.dbj_status_payload = None
    args.dbj_status_error = None
    args.dbj_role_values = []
    args.dbj_level_values = []
    if "dailyberlinjobs" in requested:
        args.dbj_skipped = not is_daily_berlin_jobs_location(args.where)
        if not args.dbj_skipped:
            dbj_base_url = os.environ.get("GJS_DBJ_URL", "https://daily-berlin-jobs.vercel.app").rstrip("/")
            try:
                args.dbj_status_payload = request_json(dbj_base_url + "/api/v1/status")
            except Exception as error:
                args.dbj_status_error = str(error)
            if isinstance(args.dbj_status_payload, dict):
                filters = args.dbj_status_payload.get("filters") or {}
                args.dbj_role_values = validate_dbj_filter_values(
                    args.dbj_role, filters.get("roles") or [], "--dbj-role",
                )
                args.dbj_level_values = validate_dbj_filter_values(
                    args.dbj_level, filters.get("levels") or [], "--dbj-level",
                )

    source_jobs: dict[str, list[dict[str, Any]]] = {}
    source_errors: dict[str, list[str]] = {}
    source_received_counts: dict[str, int] = {}
    source_metadata: dict[str, dict[str, Any]] = {}
    for source in requested:
        try:
            fetched = SOURCE_FETCHERS[source](args)
            if len(fetched) == 3:
                results, errors, metadata = fetched
                source_metadata[source] = metadata
            else:
                results, errors = fetched
                source_metadata[source] = {}
            source_jobs[source] = results
            source_errors[source] = list(errors)
        except Exception as error:
            source_jobs[source] = []
            source_errors[source] = [str(error)]
            source_metadata[source] = {}
        source_received_counts[source] = len(source_jobs[source])

    statuses: dict[str, dict[str, Any]] = {}
    all_jobs = []
    for source, values in source_jobs.items():
        filtered_source = filter_jobs(values, args)
        statuses[source] = source_status(
            source, len(filtered_source), source_errors[source],
            produced_count=source_received_counts[source],
        )
        statuses[source].update(source_metadata.get(source, {}))
        all_jobs.extend(filtered_source)
    title_filtered = 0
    if args.title_match:
        matched_jobs = [job for job in all_jobs if title_matches(args.what, job.get("title"))]
        title_filtered = len(all_jobs) - len(matched_jobs)
        all_jobs = matched_jobs
    for job in all_jobs:
        job.pop("_tags", None)
    unique = deduplicate_jobs(all_jobs)
    unique = sorted(unique, key=lambda job: job.get("posted") or "", reverse=True)
    unique = mark_seen_jobs(unique, args.seen_file, args.only_new, args.limit)
    meta = {
        "query": {
            "what": args.what, "where": args.where, "radius": args.radius, "days": args.days,
            "sources": requested, "remote": args.remote, "english_only": args.english_only,
            "limit": args.limit, "max_pages": args.max_pages, "linkedin_pages": args.linkedin_pages,
            "dbj_role": args.dbj_role, "dbj_level": args.dbj_level, "only_new": args.only_new,
        },
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sources": statuses,
    }
    if args.title_match:
        meta["title_filtered"] = title_filtered
    return {"meta": meta, "jobs": unique}


def main() -> int:
    parser = argparse.ArgumentParser(description="Search public German job sources, ATS boards, and optional aggregators.")
    parser.add_argument("--what", required=True, help="Job keywords, e.g. 'software engineer'")
    parser.add_argument("--where", default=None, help="City or region")
    parser.add_argument("--radius", type=int, default=25, help="BA search radius in km (default: 25)")
    parser.add_argument("--days", type=int, default=7, help="Posting age window in days (default: 7)")
    parser.add_argument("--sources", default=",".join(DEFAULT_SOURCES),
                        help="Comma-separated sources: dailyberlinjobs (Daily Berlin Jobs (Berlin tech jobs)), ba,arbeitnow,ats,linkedin,freehire (default: dailyberlinjobs,ba,arbeitnow,ats,linkedin)")
    parser.add_argument("--companies", default=str(DEFAULT_COMPANIES),
                        help="ATS company catalog JSON (default: ../assets/companies.json)")
    parser.add_argument("--remote", action="store_true", help="Keep explicitly remote postings")
    parser.add_argument("--english-only", action="store_true", help="Drop ads where German is required")
    parser.add_argument("--title-match", action="store_true",
                        help="Keep jobs whose title matches the normalized query phrase")
    parser.add_argument("--limit", type=int, default=100, help="Maximum number of output jobs (default: 100)")
    parser.add_argument("--max-pages", type=int, default=3,
                        help="Maximum pages for BA, Arbeitnow, and Freehire (default: 3)")
    parser.add_argument("--linkedin-pages", type=int, default=1,
                        help="LinkedIn pages per query, capped at 5 (default: 1)")
    parser.add_argument("--dbj-role", help="Daily Berlin Jobs (Berlin tech jobs) role filter (comma-separated status values)")
    parser.add_argument("--dbj-level", help="Daily Berlin Jobs (Berlin tech jobs) level filter (comma-separated status values)")
    parser.add_argument("--seen-file", help="Local JSON memory of previously seen job URLs and identities")
    parser.add_argument("--only-new", action="store_true", help="Show only jobs not present in --seen-file")
    parser.add_argument("--format", choices=("md", "json", "csv"), default="md", metavar="md|json|csv",
                        help="Output format (default: md)")
    parser.add_argument("--out", help="Write output to this file as well as stdout")
    parser.add_argument("--with-description", action="store_true",
                        help="Include descriptions (max 4000 chars; LinkedIn detail for first 10 hits)")
    args = parser.parse_args()
    requested = [source.strip().casefold() for source in args.sources.split(",") if source.strip()]
    if "linkedin" in requested:
        print(
            "LinkedIn guest access is personal-use only, low-volume; automated access violates LinkedIn's User Agreement.",
            file=sys.stderr,
        )
    try:
        result = run_search(vars(args))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    jobs = result["jobs"]
    statuses = result["meta"]["sources"]
    if args.format == "json":
        output = json_text(result)
    elif args.format == "csv":
        fields = ["id", "source", "title", "company", "location", "remote", "url", "posted", "employment_type", "salary", "ad_language", "german_required", "new", "integrity_ok", "integrity_issues"]
        if args.with_description:
            fields.append("description")
        csv_jobs = [{**job, "integrity_ok": job.get("integrity", {}).get("ok", True),
                     "integrity_issues": json_text(job.get("integrity", {}).get("issues", []), indent=None)} for job in jobs]
        output = csv_text(csv_jobs, fields)
    else:
        output = format_markdown(jobs, statuses)
    write_output(output, args.out)
    for source, status in statuses.items():
        print(source_status_line(source, status), file=sys.stderr)
    for index, job in enumerate(jobs):
        if not job.get("integrity", {}).get("ok", True):
            print("Integrity warning: posting {} requires review.".format(index + 1), file=sys.stderr)
    if args.title_match:
        count = result["meta"]["title_filtered"]
        print("title-match: dropped {} job{}".format(count, "" if count == 1 else "s"), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
