import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "berlin-job-search" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _common import (
    ba_location_name,
    canonical_location_name,
    city_location_matches,
    linkedin_location_and_geo_id,
    salary_text,
)


class CityAliasTests(unittest.TestCase):
    def test_city_spellings_share_ba_linkedin_and_location_aliases(self):
        cases = (
            (("München", "Munich", "Muenchen"), "München", "Munich, Germany", "100495942", "München, Bayern"),
            (("Köln", "Cologne", "Koeln"), "Köln", "Cologne, Germany", "90009713", "Köln, Nordrhein-Westfalen"),
            (("Nürnberg", "Nuremberg", "Nuernberg"), "Nürnberg", "Nuremberg, Germany", "101768819", "Nürnberg, Bayern"),
            (("Frankfurt", "Frankfurt am Main"), "Frankfurt am Main", "Frankfurt am Main, Germany", "90009714", "Frankfurt am Main, Hessen"),
            (("Düsseldorf", "Duesseldorf"), "Düsseldorf", "Düsseldorf, Germany", "104008204", "Düsseldorf, Nordrhein-Westfalen"),
            (("Hamburg",), "Hamburg", "Hamburg, Germany", "101949806", "Hamburg, Germany"),
            (("Berlin",), "Berlin", "Berlin, Germany", "103035651", "Berlin, Berlin"),
            (("Stuttgart",), "Stuttgart", "Stuttgart, Germany", "102473731", "Stuttgart, Baden-Württemberg"),
            (("Leipzig",), "Leipzig", "Leipzig, Germany", "106955537", "Leipzig, Sachsen"),
            (("Dresden",), "Dresden", "Dresden, Germany", "104815888", "Dresden, Sachsen"),
            (("Hannover", "Hanover"), "Hannover", "Hanover, Germany", "100423791", "Hannover, Niedersachsen"),
        )
        for variants, ba_name, linkedin_location, geo_id, result_location in cases:
            for variant in variants:
                with self.subTest(city=variant):
                    self.assertEqual(ba_location_name(variant), ba_name)
                    self.assertEqual(
                        linkedin_location_and_geo_id(variant),
                        (linkedin_location, geo_id),
                    )
                    self.assertTrue(city_location_matches(variant, result_location))

    def test_city_location_filter_accepts_localized_result_for_english_query(self):
        self.assertTrue(city_location_matches("Munich", "München, Bayern"))
        self.assertTrue(city_location_matches("München", "Munich, Bavaria, Germany"))
        self.assertTrue(city_location_matches("Frankfurt", "Frankfurt am Main, Hessen"))
        self.assertFalse(city_location_matches("Munich", "Nürnberg, Bayern"))

    def test_unknown_city_is_passed_through_without_geo_id(self):
        self.assertEqual(ba_location_name("Cottbus"), "Cottbus")
        self.assertEqual(canonical_location_name("Cottbus, Brandenburg"), "Cottbus, Brandenburg")
        self.assertEqual(linkedin_location_and_geo_id("Cottbus"), ("Cottbus", None))
        self.assertTrue(city_location_matches("Cottbus", "Cottbus, Brandenburg"))

    def test_verified_germany_linkedin_geo_id_is_reused(self):
        self.assertEqual(ba_location_name("Germany"), "Deutschland")
        self.assertEqual(linkedin_location_and_geo_id("Germany"), ("Germany", "101282230"))


class CommonSalaryTests(unittest.TestCase):
    def test_salary_text_rejects_amountless_labels(self):
        self.assertIsNone(salary_text("EUR YEAR"))
        self.assertIsNone(salary_text({"label": "EUR YEAR"}))
        self.assertEqual(salary_text({"minValue": 50000, "maxValue": 65000}), "50000–65000 EUR")


if __name__ == "__main__":
    unittest.main()
