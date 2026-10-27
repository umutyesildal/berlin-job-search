import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import validate_skill


class ReadmeLinkTests(unittest.TestCase):
    def test_broken_relative_link_in_german_readme_names_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# English\n", encoding="utf-8")
            (root / "README.de.md").write_text(
                "# Deutsch\n\n[Fehlerhafter Link](missing.md)\n",
                encoding="utf-8",
            )
            with patch.object(validate_skill, "ROOT", root.resolve()):
                self.assertEqual(
                    validate_skill.validate_readme_links(),
                    ["README.de.md:3 broken relative link: missing.md"],
                )


if __name__ == "__main__":
    unittest.main()
