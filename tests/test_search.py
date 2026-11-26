import contextlib
import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "berlin-job-search" / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))

import search
from search import (
    DEFAULT_SOURCES,
    SOURCE_FETCHERS,
    canonical_url,
    dedup_key,
    deduplicate_jobs,
    dailyberlinjobs_query_term,
    fetch_ba_source,
    fetch_dailyberlinjobs,
    filter_jobs,
    freehire_salary,
    is_daily_berlin_jobs_location,
    fetch_eurojobs,
    fetch_freehire,
    parse_arbeitnow,
    parse_ashby,
    parse_ba,
    parse_dailyberlinjobs,
    parse_eurojobs,
    parse_freehire,
    parse_greenhouse,
    parse_lever,
    parse_recruitee,
    parse_personio_xml,
    parse_smartrecruiters,
    parse_workable,
    parse_join,
    mark_seen_jobs,
    make_job,
    run_search,
    source_status,
    title_match_terms,
    title_matches,
    validate_dbj_filter_values,
)
from linkedin import parse_job_cards


def fixture_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class SourceParsingTests(unittest.TestCase):
    def test_source_registry_defaults_linkedin_and_keeps_freehire_opt_in(self):
        self.assertIn("linkedin", DEFAULT_SOURCES)
        self.assertEqual(DEFAULT_SOURCES[0], "dailyberlinjobs")
        self.assertNotIn("freehire", DEFAULT_SOURCES)
        self.assertIn("linkedin", SOURCE_FETCHERS)
        self.assertIn("dailyberlinjobs", SOURCE_FETCHERS)
        self.assertIn("freehire", SOURCE_FETCHERS)

    def test_daily_berlin_jobs_fixture_maps_source_metadata(self):
        jobs = parse_dailyberlinjobs(fixture_json("dbj_jobs.json"))
        self.assertEqual(len(jobs), 2)
        linked_in, ats = jobs
        self.assertEqual(linked_in["source"], "dailyberlinjobs")
        self.assertEqual(linked_in["via"], "linkedin")
        self.assertEqual(linked_in["remote"], None)
        self.assertEqual(linked_in["role"], "Fullstack")
        self.assertEqual(linked_in["tech_stack"], ["JavaScript", "Python", "SQL"])
        self.assertEqual(linked_in["posted"], "2026-10-23")
        self.assertEqual(ats["via"], "ats")
        self.assertFalse(ats["remote"])

    def test_daily_berlin_jobs_query_uses_candidate_word_then_title_all_words(self):
        self.assertEqual(dailyberlinjobs_query_term("software engineer"), "software")
        self.assertEqual(dailyberlinjobs_query_term("python"), "python")

    def test_run_search_accepts_plain_params_without_argparse(self):
        status = fixture_json("dbj_status.json")
        jobs = fixture_json("dbj_jobs.json")
        with patch.object(search, "request_json", side_effect=[status, jobs]) as request:
            result = run_search({
                "what": "software engineer",
                "where": "Berlin",
                "days": 3,
                "sources": "dailyberlinjobs",
                "limit": 10,
            })
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result["meta"]["sources"]["dailyberlinjobs"]["updated_at"], status["updated_at"])
        self.assertEqual(len(result["jobs"]), 1)

    def test_daily_berlin_jobs_role_and_level_validation(self):
        filters = fixture_json("dbj_status.json")["filters"]
        self.assertEqual(
            validate_dbj_filter_values("fullstack,software engineering", filters["roles"], "--dbj-role"),
            ["Fullstack", "Software Engineering"],
        )
        self.assertEqual(
            validate_dbj_filter_values("senior,staff / principal", filters["levels"], "--dbj-level"),
            ["Senior", "Staff / Principal"],
        )
        with self.assertRaisesRegex(ValueError, "Allowed values:"):
            validate_dbj_filter_values("unknown", filters["roles"], "--dbj-role")
        argv = [
            "search.py", "--what", "python", "--where", "Berlin",
            "--days", "3", "--sources", "dailyberlinjobs", "--dbj-role", "unknown",
        ]
        stderr = io.StringIO()
        with patch.object(search, "request_json", return_value=fixture_json("dbj_status.json")) as request, \
                patch.object(sys, "argv", argv), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                search.main()
        request.assert_called_once()
        self.assertIn("Allowed values: Backend, Data / AI / ML", stderr.getvalue())

    def test_daily_berlin_jobs_search_uses_status_once_and_maps_filters(self):
        status_payload = fixture_json("dbj_status.json")
        jobs_payload = fixture_json("dbj_jobs.json")
        argv = [
            "search.py", "--what", "software engineer", "--where", "Berlin",
            "--days", "3", "--sources", "dailyberlinjobs", "--dbj-role", "fullstack",
            "--dbj-level", "not specified", "--format", "json",
        ]
        stdout = io.StringIO()
        with patch.object(
            search, "request_json", side_effect=[status_payload, jobs_payload],
        ) as request, patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(search.main(), 0)
        self.assertEqual(request.call_count, 2)
        self.assertTrue(request.call_args_list[0].args[0].endswith("/api/v1/status"))
        params = parse_qs(urlsplit(request.call_args_list[1].args[0]).query)
        self.assertEqual(params["q"], ["software"])
        self.assertEqual(params["limit"], ["500"])
        self.assertEqual(params["role"], ["Fullstack"])
        self.assertEqual(params["level"], ["Not specified"])
        since = datetime.fromisoformat(params["since"][0].replace("Z", "+00:00"))
        self.assertAlmostEqual(
            (datetime.now(timezone.utc) - timedelta(days=3) - since).total_seconds(),
            0,
            delta=3,
        )
        payload = json.loads(stdout.getvalue())
        status = payload["meta"]["sources"]["dailyberlinjobs"]
        self.assertEqual(status["updated_at"], status_payload["updated_at"])
        self.assertFalse(status["stale"])
        self.assertTrue(status["ok"])
        self.assertEqual(len(payload["jobs"]), 1)
        self.assertEqual(payload["jobs"][0]["via"], "linkedin")

    def test_daily_berlin_jobs_description_hydration_is_capped_at_ten(self):
        template = fixture_json("dbj_jobs.json")["jobs"][1]
        rows = []
        for index in range(12):
            rows.append({
                **template,
                "id": 20000 + index,
                "title": "Software Engineer {}".format(index),
                "url": "https://jobs.example/dbj/{}".format(index),
                "detail_url": "https://daily-berlin-jobs.vercel.app/api/v1/jobs/{}".format(20000 + index),
            })
        payload = {"updated_at": "2026-10-23T14:05:51.275Z", "count": 12, "jobs": rows}
        detail = {
            "description_available": True,
            "description": "<p>Software engineers work with our product team.</p>",
            "employment_type": "Full-time",
        }
        args = Namespace(
            dbj_skipped=False, dbj_status_error=None, dbj_status_payload=fixture_json("dbj_status.json"),
            dbj_role_values=[], dbj_level_values=[], where="Berlin", what="software engineer",
            days=7, remote=False, max_pages=1, with_description=True,
        )
        with patch.object(search, "request_json", side_effect=[payload] + [detail] * 10) as request:
            jobs, errors, _ = fetch_dailyberlinjobs(args)
        self.assertEqual(request.call_count, 11)
        self.assertEqual(len(jobs), 12)
        self.assertEqual(errors, [])
        self.assertTrue(all(job.get("description") for job in jobs[:10]))
        self.assertTrue(all(job.get("description") is None for job in jobs[10:]))

    def test_daily_berlin_jobs_remote_option_maps_to_remote_work_mode(self):
        args = Namespace(
            dbj_skipped=False, dbj_status_error=None, dbj_status_payload=fixture_json("dbj_status.json"),
            dbj_role_values=[], dbj_level_values=[], where="Berlin", what="python",
            days=3, remote=True, max_pages=1, limit=100, with_description=False,
        )
        with patch.object(search, "request_json", return_value={"jobs": [], "next_cursor": None}) as request:
            jobs, errors, _ = fetch_dailyberlinjobs(args)
        params = parse_qs(urlsplit(request.call_args.args[0]).query)
        self.assertEqual(params["work_mode"], ["Remote"])
        self.assertEqual(jobs, [])
        self.assertEqual(errors, [])

    def test_daily_berlin_jobs_non_berlin_skip_makes_no_requests(self):
        self.assertTrue(is_daily_berlin_jobs_location(None))
        self.assertTrue(is_daily_berlin_jobs_location("BERLIN, Germany"))
        self.assertFalse(is_daily_berlin_jobs_location("München"))
        argv = [
            "search.py", "--what", "python", "--where", "München",
            "--days", "3", "--sources", "dailyberlinjobs", "--format", "json",
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(search, "request_json") as request, \
                patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(search.main(), 0)
        request.assert_not_called()
        status = json.loads(stdout.getvalue())["meta"]["sources"]["dailyberlinjobs"]
        self.assertTrue(status["ok"])
        self.assertEqual(status["count"], 0)
        self.assertIsNone(status["error"])
        self.assertEqual(status["partial_errors"], [])
        self.assertEqual(status["skipped"], "Berlin-only source")

    def test_dedup_prefers_descriptions_then_daily_berlin_jobs_metadata(self):
        linkedin = make_job(
            "linkedin", "Software Engineer", "Example", "Berlin",
            "https://linkedin.com/jobs/view/1",
        )
        dbj = make_job(
            "dailyberlinjobs", "Software Engineer", "Example", "Berlin",
            "https://www.linkedin.com/jobs/view/1?utm_source=daily-berlin-jobs",
        )
        dbj.update({"via": "linkedin", "role": "Backend", "level": "Senior", "tech_stack": ["Python"]})
        preferred_dbj = deduplicate_jobs([linkedin, dbj])[0]
        self.assertEqual(preferred_dbj["source"], "dailyberlinjobs")
        self.assertEqual(preferred_dbj["role"], "Backend")
        linkedin["description"] = "A real posting description."
        self.assertEqual(deduplicate_jobs([dbj, linkedin])[0]["description"], "A real posting description.")

    def test_ats_partial_company_failure_is_reported_without_failing_source(self):
        companies = [
            {"name": "Healthy Co", "ats": "greenhouse", "slug": "healthy"},
            {"name": "Failing Co", "ats": "greenhouse", "slug": "failing"},
        ]

        def fetch_company(company, include_description=False):
            if company["name"] == "Failing Co":
                raise RuntimeError("read timed out")
            return [make_job(
                "ats", "Software Engineer", "Healthy Co", "Berlin",
                "https://jobs.example/healthy/software-engineer",
            )]

        with tempfile.TemporaryDirectory() as directory:
            companies_path = Path(directory) / "companies.json"
            companies_path.write_text(json.dumps(companies), encoding="utf-8")
            argv = [
                "search.py", "--what", "software engineer", "--where", "Berlin",
                "--days", "0", "--sources", "ats", "--companies", str(companies_path),
                "--format", "json",
            ]
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch.object(search, "ats_jobs", side_effect=fetch_company), \
                    patch.object(sys, "argv", argv), \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(search.main(), 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(len(payload["jobs"]), 1)
        status = payload["meta"]["sources"]["ats"]
        self.assertTrue(status["ok"])
        self.assertEqual(status["count"], 1)
        self.assertIsNone(status["error"])
        self.assertEqual(status["partial_errors"], ["Failing Co: read timed out"])
        self.assertIn("ats: ok, 1 job (1 company error)", stderr.getvalue())

    def test_source_status_fails_only_when_no_results_and_errors(self):
        self.assertTrue(source_status("ats", 0, [])["ok"])
        partial = source_status("ats", 36, ["Octopus Energy: read timed out"])
        self.assertTrue(partial["ok"])
        self.assertEqual(partial["error"], None)
        self.assertEqual(partial["partial_errors"], ["Octopus Energy: read timed out"])
        filtered_empty = source_status(
            "ats", 0, ["Octopus Energy: read timed out"], produced_count=36,
        )
        self.assertTrue(filtered_empty["ok"])
        self.assertEqual(filtered_empty["count"], 0)
        failed = source_status("ats", 0, ["Octopus Energy: read timed out"])
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["error"], "Octopus Energy: read timed out")

    def test_freehire_retains_first_page_when_later_page_fails(self):
        payload = fixture_json("freehire.json")
        payload["data"][0]["location"] = "Berlin, Germany"
        args = Namespace(
            limit=100, max_pages=2, where="Berlin", with_description=False,
            remote=False, what="software engineer", days=0,
        )
        with patch.object(
            search, "request_json",
            side_effect=[payload, RuntimeError("read timed out")],
        ):
            jobs, errors = fetch_freehire(args)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["location"], "Berlin, Germany")
        self.assertEqual(errors, ["page 2: read timed out"])

    def test_ba_v6_fixture(self):
        jobs = parse_ba(fixture_json("ba_v6.json"))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["source"], "ba")
        self.assertEqual(jobs[0]["location"], "Berlin, 10115")
        self.assertEqual(jobs[0]["id"], "12336-a26f539j0456360-S")
        self.assertEqual(jobs[0]["remote"], False)
        self.assertEqual(jobs[0]["employment_type"], "Vollzeit")
        self.assertIsNone(jobs[0]["salary"])
        self.assertIn("/jobsuche/jobdetail/", jobs[0]["url"])
        self.assertEqual(jobs[0]["ad_language"], "unknown")

    def test_arbeitnow_fixture(self):
        jobs = parse_arbeitnow(fixture_json("arbeitnow.json"))
        self.assertEqual(jobs[0]["company"], "Ordio GmbH")
        self.assertEqual(jobs[0]["location"], "Cologne")
        self.assertEqual(jobs[0]["remote"], False)
        self.assertIn("description", parse_arbeitnow(fixture_json("arbeitnow.json"), True)[0])

    def test_greenhouse_fixture(self):
        company = {"name": "Raisin", "slug": "raisin"}
        jobs = parse_greenhouse(fixture_json("greenhouse.json"), company)
        self.assertEqual(jobs[0]["title"], "AI Platform Engineer (m/f/d)")
        self.assertEqual(jobs[0]["location"], "Berlin, Berlin, Germany")
        self.assertEqual(jobs[0]["employment_type"], "Permanent Employee")
        self.assertEqual(jobs[0]["source"], "ats")
        empty_optional_fields = parse_greenhouse(
            {"jobs": [{"id": 1, "title": "Platform Engineer", "offices": None, "metadata": None}]},
            company,
        )
        self.assertEqual(empty_optional_fields[0]["location"], "")

    def test_lever_fixture(self):
        jobs = parse_lever(fixture_json("lever.json"), {"name": "Pipedrive"})
        self.assertEqual(jobs[0]["company"], "Pipedrive")
        self.assertEqual(jobs[0]["remote"], False)
        self.assertEqual(jobs[0]["employment_type"], "Full Time")

    def test_ashby_fixture(self):
        jobs = parse_ashby(fixture_json("ashby.json"), {"name": "GALVANY"})
        self.assertEqual(jobs[0]["location"], "Berlin")
        self.assertEqual(jobs[0]["remote"], False)
        self.assertEqual(jobs[0]["ad_language"], "de")

    def test_personio_xml_fixture(self):
        body = (FIXTURES / "personio.xml").read_bytes()
        jobs = parse_personio_xml(body, {"name": "getquin", "career_page": "https://getquin.jobs.personio.de"})
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Content Creator (f/m/d)")
        self.assertEqual(jobs[0]["location"], "Berlin")
        self.assertEqual(jobs[0]["url"], "https://getquin.jobs.personio.de/job/1309674")
        self.assertEqual(jobs[0]["ad_language"], "en")

    def test_other_supported_ats_parsers(self):
        company = {"name": "Example", "slug": "example", "career_page": "https://example.test"}
        smart = parse_smartrecruiters({
            "content": [{
                "id": "sr-1", "name": "Backend Engineer",
                "location": {"city": "Berlin", "country": "Germany"},
                "applyUrl": "https://careers.smartrecruiters.com/example/sr-1",
                "releasedDate": "2026-10-20",
                "typeOfEmployment": {"label": "Full-time"},
                "jobAd": {"sections": {"jobDescription": {"text": "English is our working language."}}},
            }]
        }, company)
        self.assertEqual(smart[0]["location"], "Berlin, Germany")
        self.assertEqual(smart[0]["employment_type"], "Full-time")
        self.assertEqual(smart[0]["german_required"], "not_required")

        recruitee = parse_recruitee({
            "offers": [{
                "id": "r-1", "title": "Designer", "locations": [{"city": "Hamburg", "country": "Germany"}],
                "careers_url": "https://example.recruitee.com/o/designer", "remote": False,
            }]
        }, company)
        self.assertEqual(recruitee[0]["location"], "Hamburg, Germany")
        self.assertFalse(recruitee[0]["remote"])

        workable = parse_workable({
            "jobs": [{
                "shortcode": "w-1", "title": "Data Analyst",
                "locations": [{"city": "Munich", "country": "Germany"}],
                "url": "https://apply.workable.com/example/j/w-1", "telecommuting": True,
            }]
        }, company)
        self.assertTrue(workable[0]["remote"])
        self.assertEqual(workable[0]["location"], "Munich, Germany")

        joined = parse_join({
            "items": [{
                "id": "j-1", "idParam": "engineer", "title": "Engineer",
                "city": {"cityName": "Berlin"}, "country": {"name": "Germany"},
                "workplaceType": "HYBRID",
            }]
        }, {"name": "JOIN", "slug": "54"})
        self.assertEqual(joined[0]["location"], "Berlin, Germany")
        self.assertEqual(joined[0]["remote"], False)

    def test_linkedin_card_fixture(self):
        cards = parse_job_cards((FIXTURES / "linkedin_card.html").read_text(encoding="utf-8"))
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card["title"], "Senior Software Engineer (f/m/d)")
        self.assertEqual(card["company"], "Upvest")
        self.assertEqual(card["location"], "Berlin, Germany")
        self.assertEqual(card["date"], "2026-10-22")
        self.assertIn("linkedin.com/jobs/view", card["url"])

    def test_canonical_url_drops_tracking_and_fragment(self):
        first = "https://Example.com/jobs/42/?utm_source=mail&ref=jobs#apply"
        self.assertEqual(canonical_url(first), "https://example.com/jobs/42?ref=jobs")
        self.assertEqual(canonical_url("https://example.com/jobs/42?ref=jobs"), canonical_url(first))

    def test_dedup_key_normalizes_gender_markers_and_accents(self):
        one = {"company": "Café GmbH", "title": "Engineer (m/w/d)", "location": "Köln, Germany", "url": ""}
        two = {"company": "Cafe GmbH", "title": "Engineer (all genders)", "location": "Koln", "url": ""}
        self.assertEqual(dedup_key(one)[1], dedup_key(two)[1])

    def test_remote_english_and_keyword_filters(self):
        jobs = [
            {"source": "ats", "title": "Senior Software Engineer", "location": "Berlin",
             "remote": True, "german_required": "nice_to_have", "posted": None},
            {"source": "ats", "title": "Software Engineer", "location": "Berlin",
             "remote": True, "german_required": "required", "posted": None},
            {"source": "ats", "title": "Software Engineer", "location": "Berlin",
             "remote": False, "german_required": "unknown", "posted": None},
        ]
        args = Namespace(days=0, remote=True, english_only=True, where="Berlin",
                         what="software engineer", sources=["ats"])
        filtered = filter_jobs(jobs, args)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["german_required"], "nice_to_have")

    def test_freehire_fixture(self):
        payload = fixture_json("freehire.json")
        jobs = parse_freehire(payload, include_description=True)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["source"], "freehire")
        self.assertEqual(jobs[0]["id"], "software-engineer-157-musarubra-software-india-private-limited-tg5erlfn")
        self.assertEqual(jobs[0]["remote"], False)
        self.assertIsNone(jobs[0]["salary"])
        self.assertIn("description", jobs[0])
        self.assertEqual(jobs[0]["ad_language"], "en")
        self.assertEqual(
            freehire_salary({"salary_min": 90000, "salary_max": 120000, "salary_currency": "EUR", "salary_period": "year"}),
            "EUR 90000–120000/year",
        )

    def test_seen_file_marks_urls_and_identities_once(self):
        import tempfile

        first = {"company": "Café GmbH", "title": "Engineer (m/w/d)", "location": "Köln",
                 "url": "https://example.com/jobs/1?utm_source=mail"}
        second = {"company": "Cafe GmbH", "title": "Engineer (all genders)", "location": "Koln",
                  "url": "https://example.com/jobs/1/"}
        with tempfile.TemporaryDirectory() as directory:
            seen_file = str(Path(directory) / "seen.json")
            self.assertTrue(mark_seen_jobs([first], seen_file)[0]["new"])
            self.assertFalse(mark_seen_jobs([second], seen_file)[0]["new"])
            repeated = mark_seen_jobs([second], seen_file, only_new=True)
            self.assertEqual(repeated, [])
            memory = json.loads(Path(seen_file).read_text(encoding="utf-8"))
            self.assertTrue(any(key.startswith("url:") for key in memory["seen"]))
            self.assertTrue(any(key.startswith("identity:") for key in memory["seen"]))
            self.assertTrue(all(len(value) == 10 for value in memory["seen"].values()))

            limited_file = str(Path(directory) / "limited.json")
            candidate_one = {"company": "One", "title": "Role", "location": "Berlin",
                             "url": "https://one.test/role"}
            candidate_two = {"company": "Two", "title": "Role", "location": "Berlin",
                             "url": "https://two.test/role"}
            self.assertEqual(len(mark_seen_jobs([candidate_one, candidate_two], limited_file, limit=1)), 1)
            only_unseen = mark_seen_jobs(
                [candidate_one, candidate_two], limited_file, only_new=True, limit=1,
            )
            self.assertEqual([job["company"] for job in only_unseen], ["Two"])


class CitySearchTests(unittest.TestCase):
    def test_ba_uses_the_same_german_query_for_munich_aliases(self):
        requests = []
        for location in ("Munich", "München"):
            args = Namespace(
                what="Product Manager",
                where=location,
                radius=25,
                days=14,
                max_pages=1,
                with_description=False,
            )
            with patch.object(
                search,
                "request_json",
                return_value={"ergebnisliste": [], "maxErgebnisse": 0},
            ) as request:
                jobs, errors = fetch_ba_source(args)
            self.assertEqual(jobs, [])
            self.assertEqual(errors, [])
            requests.append(parse_qs(urlsplit(request.call_args.args[0]).query))

        self.assertEqual(requests[0], requests[1])
        self.assertEqual(requests[0]["wo"], ["München"])

    def test_eurojobs_is_opt_in(self):
        self.assertNotIn("eurojobs", DEFAULT_SOURCES)
        self.assertIn("eurojobs", SOURCE_FETCHERS)

    def test_eurojobs_fixture_links_to_job_page_not_apply_redirect(self):
        jobs = parse_eurojobs(fixture_json("eurojobs.json"))
        self.assertEqual(len(jobs), 2)
        hybrid, remote = jobs
        self.assertEqual(hybrid["source"], "eurojobs")
        self.assertEqual(hybrid["id"], "900001")
        self.assertEqual(hybrid["company"], "Example Labs GmbH")
        self.assertEqual(hybrid["location"], "Berlin, Germany")
        self.assertTrue(hybrid["url"].startswith("https://eurojobs.ai/jobs/companies/"))
        self.assertNotIn("/apply/", hybrid["url"])
        self.assertIs(hybrid["remote"], False)
        self.assertEqual(hybrid["work_mode"], "hybrid")
        self.assertEqual(hybrid["posted"], "2026-11-20")
        self.assertEqual(hybrid["employment_type"], "full-time")
        self.assertEqual(hybrid["salary"], "EUR 70000–90000/year")
        self.assertEqual(hybrid["skills"], ["python", "postgresql", "aws"])
        self.assertIs(hybrid["visa_sponsorship"], True)
        self.assertNotIn("description", hybrid)
        self.assertIs(remote["remote"], True)
        self.assertEqual(remote["posted"], "2026-11-18")
        self.assertIsNone(remote["salary"])
        self.assertEqual(remote["german_required"], "required")

    def test_eurojobs_request_params_and_pagination(self):
        payload = fixture_json("eurojobs.json")
        payload["meta"]["total"] = 150
        args = Namespace(
            what="backend engineer", where="M\u00fcnchen", days=7, max_pages=3,
            with_description=False, remote=True,
        )
        with patch.object(search, "request_json", side_effect=[payload, payload]) as request:
            jobs, errors = fetch_eurojobs(args)
        self.assertEqual(errors, [])
        self.assertEqual(len(jobs), 4)
        self.assertEqual(request.call_count, 2)
        first = urlsplit(request.call_args_list[0].args[0])
        self.assertEqual(first.netloc, "eurojobs.ai")
        self.assertEqual(first.path, "/api/v1/jobs")
        query = parse_qs(first.query)
        self.assertEqual(query["q"], ["backend engineer"])
        self.assertEqual(query["country"], ["DE"])
        self.assertEqual(query["location"], ["munich"])
        self.assertEqual(query["since"], ["7d"])
        self.assertEqual(query["remote"], ["true"])
        self.assertEqual(query["per_page"], ["100"])
        self.assertEqual(query["page"], ["1"])
        self.assertEqual(parse_qs(urlsplit(request.call_args_list[1].args[0]).query)["page"], ["2"])

    def test_eurojobs_country_wide_search_omits_location_and_long_windows(self):
        args = Namespace(
            what="data engineer", where="Germany", days=90, max_pages=1,
            with_description=False, remote=False,
        )
        with patch.object(search, "request_json", return_value={"data": [], "meta": {"total": 0}}) as request:
            jobs, errors = fetch_eurojobs(args)
        self.assertEqual((jobs, errors), ([], []))
        query = parse_qs(urlsplit(request.call_args.args[0]).query)
        self.assertNotIn("location", query)
        self.assertNotIn("since", query)
        self.assertNotIn("remote", query)

    def test_eurojobs_retains_first_page_when_later_page_fails(self):
        payload = fixture_json("eurojobs.json")
        payload["meta"]["total"] = 500
        args = Namespace(
            what="engineer", where="Berlin", days=7, max_pages=2,
            with_description=False, remote=False,
        )
        with patch.object(search, "request_json", side_effect=[payload, RuntimeError("read timed out")]):
            jobs, errors = fetch_eurojobs(args)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(errors, ["page 2: read timed out"])

    def test_eurojobs_with_description_fetches_full_text(self):
        payload = fixture_json("eurojobs.json")
        detail = {"data": {"id": "900001", "description_html": "<p>We build fictional APIs. English is our working language.</p>"}}
        args = Namespace(
            what="engineer", where="Berlin", days=7, max_pages=1,
            with_description=True, remote=False,
        )
        with patch.object(
            search, "request_json",
            side_effect=[payload, detail, RuntimeError("HTTP 404")],
        ) as request:
            jobs, errors = fetch_eurojobs(args)
        self.assertEqual(urlsplit(request.call_args_list[1].args[0]).path, "/api/v1/jobs/900001")
        self.assertEqual(jobs[0]["description"], "We build fictional APIs. English is our working language.")
        self.assertIn("Deutschkenntnisse", jobs[1]["description"])
        self.assertEqual(errors, ["detail 900002: HTTP 404"])

    def test_freehire_uses_canonical_english_city_aliases(self):
        urls = []
        for location in ("Munich", "München"):
            args = Namespace(
                what="Product Manager",
                where=location,
                days=14,
                max_pages=1,
                limit=10,
                with_description=False,
                remote=False,
            )
            with patch.object(
                search,
                "request_json",
                return_value={"meta": {"total": 0}, "data": []},
            ) as request:
                jobs, errors = fetch_freehire(args)
            self.assertEqual(jobs, [])
            self.assertEqual(errors, [])
            urls.append(parse_qs(urlsplit(request.call_args.args[0]).query))

        self.assertEqual(urls[0], urls[1])
        self.assertEqual(urls[0]["cities"], ["Munich"])

    def test_local_filter_accepts_munich_results_labelled_munchen(self):
        job = make_job(
            "ba", "Product Manager", "Example GmbH", "München, Bayern",
            "https://example.test/product-manager",
        )
        args = Namespace(
            what="Product Manager",
            where="Munich",
            days=0,
            remote=False,
            english_only=False,
            title_match=True,
        )

        self.assertEqual(filter_jobs([job], args), [job])

    def test_search_linkedin_uses_identical_munich_alias_parameters(self):
        calls = []
        for location in ("Munich", "München"):
            args = Namespace(
                where=location,
                what="Product Manager",
                days=14,
                remote=False,
                linkedin_pages=1,
                limit=100,
                with_description=False,
            )
            with patch("linkedin.search_jobs_with_errors", return_value=([], [])) as search_jobs:
                jobs, errors = search.fetch_linkedin(args)
            self.assertEqual(jobs, [])
            self.assertEqual(errors, [])
            calls.append(search_jobs.call_args.kwargs)

        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[0]["location"], "Munich, Germany")
        self.assertEqual(calls[0]["geo_id"], "100495942")

    def test_daily_berlin_gate_uses_canonical_location(self):
        self.assertTrue(is_daily_berlin_jobs_location("Berlin, Germany"))
        self.assertFalse(is_daily_berlin_jobs_location("Munich"))
        self.assertFalse(is_daily_berlin_jobs_location("München"))


class SearchFilterTests(unittest.TestCase):
    def test_cli_requires_disclosed_salary_and_explicit_no_german_requirement(self):
        def posting(name, description):
            return search.make_job("ats", "Recruiter", name, "Berlin",
                                   "https://example.test/" + name, description=description)

        jobs = [
            posting("Eligible", "German is not required. Salary: €55,000 gross per year."),
            posting("UnknownLanguage", "Salary: €55,000 gross per year."),
            posting("UnknownPay", "German is not required. Competitive salary."),
            posting("Required", "Fluent German is required. Salary: €55,000 gross per year."),
        ]
        args = ["search.py", "--what", "Recruiter", "--sources", "ats", "--days", "0",
                "--salary-disclosed", "--no-german-required", "--format", "json"]
        out = io.StringIO()
        with patch.dict(SOURCE_FETCHERS, {"ats": lambda _: (jobs, [])}), patch("sys.argv", args):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(search.main(), 0)
        result = json.loads(out.getvalue())
        self.assertEqual([job["company"] for job in result["jobs"]], ["Eligible"])
        self.assertIn("55,000", result["jobs"][0]["salary"])

    def test_hide_tracked_matches_canonical_urls_and_city_aliases_without_writing(self):
        def posting(name, slug):
            return search.make_job("ats", "Recruiter", name, "Munich", "https://example.test/" + slug)

        jobs = [posting("Applied", "1"), posting("Rejected", "2"), posting("Withdrawn", "3"),
                posting("Saved", "4")]
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "tracker.csv"
            fixture = (
                "company,title,location,url,status,applied_date\n"
                "Applied,Original Title,,https://example.test/1?utm_source=x,applied,\n"
                "Rejected,Recruiter,München,https://other.test/2,rejected,\n"
                "Withdrawn,Recruiter,Munich,https://other.test/3,withdrawn,2026-10-01\n"
                "Saved,Recruiter,Munich,https://example.test/4,saved,\n"
            )
            path.write_text(fixture, encoding="utf-8")
            with patch.dict(SOURCE_FETCHERS, {"ats": lambda _: (jobs, [])}):
                with patch.dict("os.environ", {"GJS_DATA_DIR": root}):
                    params = {"what": "Recruiter", "sources": "ats", "days": 0}
                    self.assertEqual(len(run_search(params)["jobs"]), 4)
                    result = run_search({**params, "hide_tracked": True, "limit": 1})
            self.assertEqual(result["meta"]["tracker_filtered"], 3)
            self.assertEqual([job["company"] for job in result["jobs"]], ["Saved"])
            self.assertEqual(path.read_text(encoding="utf-8"), fixture)


class TitleMatchTests(unittest.TestCase):
    def test_title_matches_expected_phrases(self):
        cases = (
            ("Product Manager", "Senior Product Manager (m/w/d)", True),
            ("Product Manager", "Product Managerin", True),
            ("Product Manager", "Production Manager", False),
            ("Product Manager", "Product Reviewer", False),
            ("Product Owner", "Product Owner – Payments", True),
            ("Product Owner", "Business Owner", False),
            ("Business Analyst", "Business Analyst Celonis", True),
            ("Business Analyst", "Cyber Security Analyst", False),
            ("Talent Acquisition", "Talent Acquisition Manager", True),
            ("Talent Acquisition", "Working Student - Talent Acquisition", True),
            ("Talent Acquisition", "Head of Talent & Org Development", False),
            ("Recruiter", "Recruiter:in Produktion & Logistik", True),
            ("Recruiter", "Senior Recruiter (m/w/d)", True),
            ("Personalreferent", "Personalreferent:in Recruiting", True),
            ("Personalreferent", "Personalberatung Sales Manager", False),
            ("Pflegefachkraft", "Pflegefachkräfte (m/w/d)", True),
            ("Werkstudent Product Management", "Working Student Product Management", True),
            ("Talent Partner", "Senior Talent Acquisition Partner", True),
            ("Product Manager", "Product Marketing Manager", True),
            ("Personalreferent Recruiting", "Personalreferent:in Recruiting und Support", True),
            ("Product Manager", "Product Lead and Manager of Ops", False),
        )
        for query, title, expected in cases:
            with self.subTest(query=query, title=title):
                self.assertEqual(title_matches(query, title), expected)

    def test_compounds_and_normalized_german_tokens(self):
        self.assertTrue(title_matches("Produkt Manager", "Produktmanagerin"))
        self.assertTrue(title_matches("Große Straße", "Grosse Strasse"))
        self.assertTrue(title_matches("Fußpflege", "Fusspfleger"))
        self.assertEqual(title_match_terms("AI Product Manager"), (["ai", "product", "manager"], False))

    def test_all_generic_query_falls_back_to_the_full_phrase(self):
        terms, fallback = title_match_terms("Senior")
        self.assertEqual(terms, ["senior"])
        self.assertTrue(fallback)
        self.assertTrue(title_matches("Senior", "Senior Product Manager"))
        self.assertFalse(title_matches("Senior", "Manager of Products"))
        self.assertEqual(title_match_terms("Senior Manager"), (["manager"], False))

    def test_title_match_is_opt_in_and_reports_dropped_jobs(self):
        jobs = [
            make_job("ba", "Talent Acquisition Manager", "Example A", "Berlin", "https://example.test/ta"),
            make_job("ba", "Senior Software Engineer", "Example B", "Berlin", "https://example.test/software"),
        ]

        def fetcher(_args):
            return jobs, []

        with patch.dict(SOURCE_FETCHERS, {"ba": fetcher}):
            unchanged = run_search({"what": "Talent Acquisition", "where": "Berlin", "days": 0, "sources": "ba"})
            filtered = run_search({
                "what": "Talent Acquisition", "where": "Berlin", "days": 0,
                "sources": "ba", "title_match": True,
            })

        self.assertEqual([job["title"] for job in unchanged["jobs"]], ["Talent Acquisition Manager", "Senior Software Engineer"])
        self.assertNotIn("title_filtered", unchanged["meta"])
        self.assertEqual([job["title"] for job in filtered["jobs"]], ["Talent Acquisition Manager"])
        self.assertEqual(filtered["meta"]["title_filtered"], 1)

    def test_title_match_requires_the_product_owner_phrase_for_ats(self):
        jobs = [
            make_job("ats", "Product Owner (m/w/d)", "Example A", "Berlin", "https://example.test/product-owner"),
            make_job("ats", "Business Owner", "Example B", "Berlin", "https://example.test/business-owner"),
        ]
        with patch.dict(SOURCE_FETCHERS, {"ats": lambda _args: (jobs, [])}):
            result = run_search({
                "what": "Product Owner", "where": "Berlin", "days": 0,
                "sources": "ats", "title_match": True,
            })

        self.assertEqual([job["title"] for job in result["jobs"]], ["Product Owner (m/w/d)"])
        self.assertEqual(result["meta"]["title_filtered"], 1)

    def test_cli_title_match_prints_one_filter_summary_line(self):
        jobs = [
            make_job("ba", "Talent Acquisition Manager", "Example A", "Berlin", "https://example.test/ta"),
            make_job("ba", "Senior Software Engineer", "Example B", "Berlin", "https://example.test/swe"),
        ]
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = [
            "search.py", "--what", "Talent Acquisition", "--where", "Berlin",
            "--days", "0", "--sources", "ba", "--title-match", "--format", "json",
        ]
        with patch.dict(SOURCE_FETCHERS, {"ba": lambda _args: (jobs, [])}), \
                patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(search.main(), 0)

        payload = json.loads(stdout.getvalue())
        self.assertEqual([job["title"] for job in payload["jobs"]], ["Talent Acquisition Manager"])
        self.assertEqual(payload["meta"]["title_filtered"], 1)
        filter_lines = [line for line in stderr.getvalue().splitlines() if line.startswith("title-match:")]
        self.assertEqual(filter_lines, ["title-match: dropped 1 job"])

    def test_cli_markdown_format_renders_german_table_from_fictional_job(self):
        jobs = [
            make_job(
                "ba",
                "Recruiter",
                "Example Talent Studio",
                "Berlin",
                "https://example.test/jobs/recruiter",
                description="Sehr gute Deutschkenntnisse (C1), Englisch von Vorteil.",
                include_description=True,
            ),
        ]
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = [
            "search.py", "--what", "Recruiter", "--where", "Berlin",
            "--days", "0", "--sources", "ba", "--title-match", "--with-description",
            "--format", "md",
        ]
        with patch.dict(SOURCE_FETCHERS, {"ba": lambda _args: (jobs, [])}), \
                patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(search.main(), 0)

        output = stdout.getvalue()
        self.assertIn("| Title | Company | Location |", output)
        self.assertIn("| Recruiter | Example Talent Studio | Berlin", output)
        self.assertIn("| required | ba |", output)


if __name__ == "__main__":
    unittest.main()
