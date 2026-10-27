import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "berlin-job-search" / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))

from linkedin import (
    LinkedInRequestError,
    build_search_url,
    normalize_job_id,
    parse_job_cards,
    parse_job_detail,
    search_jobs_with_errors,
)


class LinkedInParsingTests(unittest.TestCase):
    def test_search_card_fixture(self):
        cards = parse_job_cards((FIXTURES / "linkedin_card.html").read_text(encoding="utf-8"))
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["id"], "4356507350")
        self.assertEqual(cards[0]["title"], "Senior Software Engineer (f/m/d)")
        self.assertEqual(cards[0]["company"], "Upvest")
        self.assertEqual(cards[0]["location"], "Berlin, Germany")
        self.assertEqual(cards[0]["date"], "2026-10-22")

    def test_detail_fixture_extracts_description_and_criteria(self):
        detail = parse_job_detail(
            (FIXTURES / "linkedin_detail.html").read_text(encoding="utf-8"),
            "4385092199",
        )
        self.assertEqual(detail["title"], "Data Engineer - Digital Health Excellence Center (w/m/d)")
        self.assertEqual(detail["company"], "EY")
        self.assertEqual(detail["seniority"], "Mid-Senior level")
        self.assertEqual(detail["employment_type"], "Full-time")
        self.assertEqual(detail["industries"], "Professional Services")
        self.assertIn("Gemeinsam die Welt", detail["description"])
        self.assertIn("german_required", detail["analysis"])
        self.assertTrue(detail["is_active"])

    def test_student_title_overrides_linkedins_full_time_criteria(self):
        markup = """\
<h2 class="topcard__title">Working Student Product Management</h2>
<h3 class="description__job-criteria-subheader">Seniority level</h3>
<span class="description__job-criteria-text">Internship</span>
<h3 class="description__job-criteria-subheader">Employment type</h3>
<span class="description__job-criteria-text">Full-time</span>
<div class="show-more-less-html__markup">Internship experience may be useful.</div>
"""
        detail = parse_job_detail(markup, "1234567890")
        self.assertEqual(detail["employment_type"], "werkstudent")
        self.assertEqual(detail["seniority"], "student")
        self.assertEqual(detail["analysis"]["employment_type"], ["werkstudent"])
        self.assertEqual(detail["analysis"]["seniority"], "student")

    def test_job_id_normalization_accepts_id_urn_and_linkedin_url(self):
        self.assertEqual(normalize_job_id("4385092199"), "4385092199")
        self.assertEqual(normalize_job_id("urn:li:jobPosting:4385092199"), "4385092199")
        self.assertEqual(
            normalize_job_id("https://de.linkedin.com/jobs/view/data-engineer-at-ey-4385092199?position=1"),
            "4385092199",
        )
        self.assertIsNone(normalize_job_id("https://linkedin.com.evil.test/jobs/view/4385092199"))

    def test_search_parameter_mapping(self):
        url = build_search_url(
            "Berlin, Germany", "data engineer", 7, None, "hybrid",
            "internship,mid-senior", "full-time,contract", True, "103035651", 2,
        )
        params = parse_qs(urlsplit(url).query)
        self.assertEqual(params["keywords"], ["data engineer"])
        self.assertEqual(params["location"], ["Berlin, Germany"])
        self.assertEqual(params["geoId"], ["103035651"])
        self.assertEqual(params["f_TPR"], ["r604800"])
        self.assertEqual(params["f_WT"], ["3"])
        self.assertEqual(params["f_E"], ["1,4"])
        self.assertEqual(params["f_JT"], ["F,C"])
        self.assertEqual(params["f_AL"], ["true"])
        self.assertEqual(params["start"], ["10"])

    def test_city_aliases_produce_identical_linkedin_parameters(self):
        urls = [
            build_search_url(
                city, "Product Manager", 14, None, None, None, None, False, None, 1,
            )
            for city in ("Munich", "München", "Muenchen")
        ]
        params = [parse_qs(urlsplit(url).query) for url in urls]
        self.assertEqual(params[0], params[1])
        self.assertEqual(params[1], params[2])
        self.assertEqual(params[0]["location"], ["Munich, Germany"])
        self.assertEqual(params[0]["geoId"], ["100495942"])

    def test_unverified_city_has_no_invented_linkedin_geo_id(self):
        url = build_search_url(
            "Nuremberg", "Product Manager", 14, None, None, None, None, False, None, 1,
        )
        params = parse_qs(urlsplit(url).query)
        self.assertEqual(params["location"], ["Nuremberg, Germany"])
        self.assertNotIn("geoId", params)

    def test_unknown_city_location_passes_through_to_linkedin(self):
        url = build_search_url(
            "Cottbus", "Product Manager", 14, None, None, None, None, False, None, 1,
        )
        params = parse_qs(urlsplit(url).query)
        self.assertEqual(params["location"], ["Cottbus"])
        self.assertNotIn("geoId", params)

    def test_search_retains_first_page_when_later_page_fails(self):
        with patch(
            "linkedin.fetch_html",
            side_effect=["page-one", LinkedInRequestError("page two timed out")],
        ), patch("linkedin.parse_job_cards", return_value=[
            {"id": str(index), "title": "Engineer", "location": "Berlin"}
            for index in range(10)
        ]):
            results, errors = search_jobs_with_errors(
                "Berlin", pages=2, limit=20,
            )
        self.assertEqual(len(results), 10)
        self.assertEqual(errors, ["page 2: page two timed out"])


class LinkedInNetworkTests(unittest.TestCase):
    def test_http_999_stops_without_retry_or_connection_switch_advice(self):
        import linkedin
        with patch('linkedin._pace'), patch('linkedin.time.sleep') as sleep, \
                patch('linkedin.request_bytes', return_value=(999, b'')) as request:
            with self.assertRaisesRegex(linkedin.LinkedInBlockedError, 'stop this source') as caught:
                linkedin.fetch_html('https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search')
        request.assert_called_once()
        sleep.assert_not_called()
        self.assertNotIn('home connection', str(caught.exception))
        self.assertNotIn('browser recipe', str(caught.exception))

    def test_transport_errors_become_linkedin_request_errors(self):
        import socket
        import urllib.error
        import linkedin
        for error in (socket.gaierror('DNS unavailable'), OSError('connection failed'),
                      TimeoutError('timed out'), urllib.error.URLError('unreachable')):
            with self.subTest(error=type(error).__name__), patch('linkedin._pace'), \
                    patch('linkedin.request_bytes', side_effect=error):
                with self.assertRaisesRegex(LinkedInRequestError, 'Could not reach LinkedIn') as caught:
                    linkedin.fetch_html('https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search')
                self.assertIs(caught.exception.__cause__, error)
                self.assertIn(str(getattr(error, 'reason', error)), str(caught.exception))

    def test_cli_handles_raw_network_errors_for_search_and_detail(self):
        import contextlib
        import io
        import json
        import socket
        import linkedin
        for args, target in ((['search', '--location', 'Berlin'], 'search_jobs'),
                             (['detail', '1234567890'], 'fetch_job_detail')):
            for error in (OSError('connection failed'), socket.gaierror('DNS unavailable')):
                stderr = io.StringIO()
                with self.subTest(args=args, error=type(error).__name__), \
                        patch.object(sys, 'argv', ['linkedin.py'] + args), \
                        patch.object(linkedin, target, side_effect=error), \
                        contextlib.redirect_stderr(stderr):
                    self.assertEqual(linkedin.main(), 1)
                output = stderr.getvalue()
                self.assertNotIn('Traceback', output)
                result = json.loads(output.splitlines()[-1])
                self.assertEqual(result['code'], 'LINKEDIN_FAILED')
                self.assertIn(str(error), result['error'])

    def test_later_dns_failure_retains_successful_search_page(self):
        import socket
        with patch('linkedin._pace'), patch('linkedin.request_bytes', side_effect=[
            (200, b'page-one'), socket.gaierror('DNS unavailable'),
        ]), patch('linkedin.parse_job_cards', return_value=[
            {'id': str(index), 'title': 'Engineer', 'location': 'Berlin'}
            for index in range(10)
        ]):
            results, errors = search_jobs_with_errors('Berlin', pages=2, limit=20)
        self.assertEqual(len(results), 10)
        self.assertEqual(len(errors), 1)
        self.assertIn('page 2: Could not reach LinkedIn', errors[0])


if __name__ == "__main__":
    unittest.main()
