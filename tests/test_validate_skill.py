import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import validate_skill


class SkillValidationTests(unittest.TestCase):
    def setUp(self):
        with contextlib.ExitStack() as cleanup:
            self.root = Path(cleanup.enter_context(tempfile.TemporaryDirectory())).resolve()
            self.skill = self.root / "skills" / "berlin-job-search"
            self.skill_path = self.skill / "SKILL.md"
            self.version = "9.8.7"
            self.frontmatter = (
                "---\nname: berlin-job-search\ndescription: Fictional validation fixture\n"
                "metadata:\n  version: '9.8.7'\n---\n"
            )
            manifests = tuple(self.root / path.relative_to(ROOT) for path in validate_skill.MANIFESTS)
            versioned = {self.root / path.relative_to(ROOT) for path in validate_skill.VERSIONED}
            cleanup.enter_context(patch.multiple(
                validate_skill, ROOT=self.root, SKILL=self.skill, MANIFESTS=manifests, VERSIONED=versioned,
            ))
            self.addCleanup(cleanup.pop_all().close)

    def write_file(self, path, contents):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    def write_valid_skill_tree(self):
        self.write_file(self.skill_path, self.frontmatter + "See references/example.md.\n")
        self.write_file(self.skill / "references" / "example.md", "Fictional job-search notes.\n")
        self.write_file(self.root / "README.md", "[Skill](skills/berlin-job-search/SKILL.md#usage)\n")
        for path in validate_skill.MANIFESTS:
            if path.name == ".mcp.json":
                manifest = {"mcpServers": {"example": {"type": "http", "url": "https://mcp.example/mcp"}}}
            elif path.name == "marketplace.json":
                manifest = {"version": self.version, "plugins": [{"version": self.version}]}
            else:
                manifest = {"version": self.version}
            self.write_file(path, json.dumps(manifest))
        for client in (".agents", ".claude"):
            link = self.root / client / "skills" / "berlin-job-search"
            link.parent.mkdir(parents=True)
            link.symlink_to("../../skills/berlin-job-search", target_is_directory=True)

    def test_main_accepts_valid_repository(self):
        self.write_valid_skill_tree()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(validate_skill.main(), 0)
        self.assertEqual(stdout.getvalue(), "Skill validation passed for version 9.8.7.\n")
        self.assertEqual(stderr.getvalue(), "")

    def test_main_reports_errors_from_each_validation_stage(self):
        self.write_valid_skill_tree()
        self.write_file(self.skill_path, self.frontmatter + "See scripts/missing.py.\n")
        self.write_file(self.root / ".claude-plugin" / "plugin.json", '{"version": "0.0.0"}')
        self.write_file(self.root / ".claude-plugin" / "marketplace.json", '{"version":"9.8.7","plugins":[{"version":"0.0.0"}]}')
        self.write_file(self.root / ".codex-plugin" / "plugin.json", "not JSON")
        self.write_file(self.root / ".mcp.json", json.dumps({
            "mcpServers": {"example": {"type": "http", "url": "http://mcp.example/mcp"}},
        }))
        (self.root / ".agents" / "skills" / "berlin-job-search").unlink()
        for path in (self.root / "notes.txt", self.root / "dist" / "ignored.txt"):
            self.write_file(path, "/".join(("", "Users", "fictional", "example.txt")))
        self.write_file(self.root / "README.md", "[Escaping link](../outside.md)\n")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(validate_skill.main(), 1)
        output = stderr.getvalue()
        for error in (
            "SKILL.md references missing path: scripts/missing.py",
            ".claude-plugin/plugin.json: version must match SKILL.md",
            ".claude-plugin/marketplace.json: plugin version must match SKILL.md",
            ".codex-plugin/plugin.json: invalid or missing JSON",
            ".mcp.json: example must use http, sse, or ws and an absolute https URL",
            ".agents/skills/berlin-job-search must resolve to skills/berlin-job-search",
            "notes.txt contains a local machine path",
            "README.md:1 link escapes repository: ../outside.md",
        ):
            self.assertIn("ERROR: " + error, output)
        self.assertIn("Skill validation failed with 8 error(s).", output)
        self.assertNotIn("dist/ignored.txt", output)

    def test_main_rejects_missing_or_unclosed_frontmatter(self):
        for contents, error in (
            ("# Example skill\n", "must start with YAML frontmatter"),
            ("---\nname: berlin-job-search\n", "has unclosed frontmatter"),
        ):
            with self.subTest(error=error):
                self.write_file(self.skill_path, contents)
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(validate_skill.main(), 1)
                self.assertEqual(stderr.getvalue(), "ERROR: skills/berlin-job-search/SKILL.md " + error + "\n")


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
