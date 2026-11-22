import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[1] / "skills" / "berlin-job-search"
sys.path.insert(0, str(SKILL / "scripts"))

from _thresholds import REQUIRED_KEYS, load_thresholds, official_amounts, render_reference


class ThresholdDataTests(unittest.TestCase):
    def test_all_thresholds_have_current_official_values(self):
        today = dt.date.today()
        missing = REQUIRED_KEYS - official_amounts(today).keys()
        self.assertFalse(missing, "Missing official {} thresholds: {}".format(today.year, sorted(missing)))

    def test_reference_matches_data(self):
        self.assertEqual(
            (SKILL / "references" / "thresholds.md").read_text(encoding="utf-8"),
            render_reference(),
            "Refresh the threshold reference using scripts/_thresholds.py",
        )

    def test_only_official_in_year_values_effective_on_the_date_are_selected(self):
        with patch("_thresholds.load_thresholds", return_value=[
            {"key": "wage", "effective_from": "2098-01-01", "status": "official", "amount": 10},
            {"key": "wage", "effective_from": "2099-01-01", "status": "calculated", "amount": 12},
            {"key": "wage", "effective_from": "2099-02-01", "status": "official", "amount": 13},
            {"key": "wage", "effective_from": "2099-03-01", "status": "official", "amount": 14},
        ]):
            self.assertEqual(official_amounts(dt.date(2099, 1, 31)), {})
            self.assertEqual(official_amounts(dt.date(2099, 2, 1)), {"wage": 13})

    def test_data_has_unique_dated_entries_and_sources(self):
        dates = set()
        for row in load_thresholds():
            with self.subTest(key=row["key"], date=row["effective_from"]):
                key = (row["key"], dt.date.fromisoformat(row["effective_from"]))
                self.assertNotIn(key, dates)
                dates.add(key)
                self.assertIn(row["key"], REQUIRED_KEYS)
                self.assertIn(row["status"], {"official", "calculated"})
                self.assertGreater(row["amount"], 0)
                self.assertTrue(row["source_url"].startswith("https://"))
                if row["status"] == "calculated":
                    self.assertTrue(row.get("basis"))
