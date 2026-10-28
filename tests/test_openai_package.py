import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import package_openai
import package_skill
from _package_blocks import select_marked_blocks


class OpenAIPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.temp_dir = Path(cls.temporary.name)
        cls.archive_path = cls.temp_dir / "berlin-job-search-openai.zip"
        package_openai.package_openai(cls.archive_path)
        cls.extract_dir = cls.temp_dir / "extracted"
        with zipfile.ZipFile(cls.archive_path) as archive:
            archive.extractall(cls.extract_dir)
        cls.package_root = cls.extract_dir / "berlin-job-search"
        cls.skill_root = cls.package_root / "skills" / "berlin-job-search"

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_markers_select_canonical_or_disabled_source_instructions(self):
        text = (
            "<!-- linkedin-guest:start -->canonical<!-- linkedin-guest:end -->"
            "<!-- linkedin-links:start --><!-- packaged --><!-- linkedin-links:end -->"
        )
        self.assertEqual(select_marked_blocks(text, set(), ".md"), "canonical")
        self.assertEqual(select_marked_blocks(text, {"linkedin"}, ".md"), "packaged")

    def test_canonical_skill_zip_keeps_existing_linkedin_instructions(self):
        output = self.temp_dir / "canonical-skill.zip"
        package_skill.package_skill(output)
        with zipfile.ZipFile(output) as archive:
            skill = archive.read("berlin-job-search/SKILL.md").decode("utf-8")
        self.assertIn("The LinkedIn source uses logged-out guest pages", skill)
        self.assertNotIn("linkedin-links:", skill)
        self.assertNotIn("ready-made search links", skill)

    def test_manifest_is_valid_and_uses_skill_version(self):
        manifest_path = "berlin-job-search/.codex-plugin/plugin.json"
        with zipfile.ZipFile(self.archive_path) as archive:
            manifest = json.loads(archive.read(manifest_path).decode("utf-8"))
        required = {
            "name", "version", "description", "author", "homepage", "repository",
            "license", "keywords", "skills", "interface", "extensions",
        }
        self.assertTrue(required.issubset(manifest))
        self.assertEqual(manifest["name"], "berlin-job-search")
        self.assertEqual(manifest["version"], package_openai.skill_version())
        self.assertNotIn("email", manifest["author"])
        interface = manifest["interface"]
        self.assertLessEqual(len(interface["shortDescription"]), 30)
        self.assertEqual(interface["supportURL"], package_openai.ISSUES_URL)
        self.assertEqual(
            manifest["extensions"]["com.openai"]["publication"]["release_notes"],
            "Initial release: job search for Berlin and Germany, German-language and "
            "Blue Card checks, Lebenslauf/CV drafting, interview practice.",
        )

    def test_packaged_search_uses_defaults_without_importing_linkedin(self):
        script = r'''
import builtins
import contextlib
import io
import json
import sys

sys.path.insert(0, sys.argv[1])
import search

assert "linkedin" not in search.DEFAULT_SOURCES
assert "linkedin" not in search.SOURCE_FETCHERS
assert "linkedin" not in sys.modules

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == "linkedin":
        raise AssertionError("disabled source attempted to import linkedin")
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
def no_jobs(args):
    return [], []

search.SOURCE_FETCHERS = {source: no_jobs for source in search.DEFAULT_SOURCES}
search.request_json = lambda *args, **kwargs: {"filters": {"roles": [], "levels": []}}
sys.argv = ["search.py", "--what", "Fictional Engineer", "--where", "Berlin", "--format", "json"]
stdout = io.StringIO()
stderr = io.StringIO()
with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
    assert search.main() == 0
result = json.loads(stdout.getvalue())
assert set(result["meta"]["sources"]) == set(search.DEFAULT_SOURCES)
assert "linkedin" not in result["meta"]["sources"]

try:
    search.run_search({"what": "Fictional Engineer", "sources": "linkedin"})
except ValueError as error:
    assert "disabled" in str(error).casefold()
else:
    raise AssertionError("requesting the disabled source did not fail clearly")

import job_detail
try:
    job_detail.fetch_detail("https://www.linkedin.com/jobs/view/123")
except ValueError as error:
    assert "disabled" in str(error).casefold()
else:
    raise AssertionError("LinkedIn detail unexpectedly remained enabled")
assert "linkedin" not in sys.modules
'''
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.skill_root / "scripts")],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_zip_layout_assets_and_linkedin_guest_search_removal(self):
        with zipfile.ZipFile(self.archive_path) as archive:
            names = archive.namelist()
            top_level = {PurePosixPath(name).parts[0] for name in names}
            self.assertEqual(top_level, {"berlin-job-search"})
            self.assertFalse(any(PurePosixPath(name).name == "linkedin.py" for name in names))
            build_data = archive.read(
                "berlin-job-search/skills/berlin-job-search/assets/build.json"
            ).decode("utf-8")
            self.assertEqual(build_data, '{"disabled_sources":["linkedin"]}\n')
            build_config = json.loads(build_data)
            self.assertEqual(build_config, {"disabled_sources": ["linkedin"]})
            for name in package_openai.ICON_NAMES:
                data = archive.read("berlin-job-search/assets/" + name)
                self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(
                    (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")),
                    (512, 512),
                )
            text = "\n".join(
                archive.read(name).decode("utf-8")
                for name in names
                if name.endswith((".md", ".py", ".json", ".yml", ".html"))
            ).casefold()
        for marker in (
            "linkedin.com/jobs-guest",
            "linkedin guest",
            "guest-card",
            "guest search",
            "logged-out guest",
            "browser-like user agent",
            "linkedin-guest:",
            "linkedin-links:",
        ):
            self.assertNotIn(marker, text)


if __name__ == "__main__":
    unittest.main()
