import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "berlin-job-search" / "scripts"
TRACKER = SCRIPTS / "tracker.py"
sys.path.insert(0, str(SCRIPTS))

import _common

GITIGNORE_CONTENT = (
    "# Created by Berlin Job Search: personal job-search data, never commit it.\n"
    "*\n"
)


def git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "Fictional Test",
        "GIT_AUTHOR_EMAIL": "fictional@example.test",
        "GIT_COMMITTER_NAME": "Fictional Test",
        "GIT_COMMITTER_EMAIL": "fictional@example.test",
    })
    return env


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)


def run_tracker_add(cwd: Path, data_dir: Optional[str] = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if data_dir is None:
        env.pop("GJS_DATA_DIR", None)
    else:
        env["GJS_DATA_DIR"] = data_dir
    return subprocess.run(
        [
            sys.executable,
            str(TRACKER),
            "add",
            "--company",
            "Fictional Employer",
            "--title",
            "Fictional Role",
            "--url",
            "https://example.test/jobs/fictional",
            "--notes",
            "x",
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


@unittest.skipUnless(shutil.which("git"), "git is required for data-directory protection tests")
class GitProtectedDataDirTests(unittest.TestCase):
    def assert_ignored_job_search(self, repo: Path, relative_data_dir: str) -> None:
        data_dir = repo / relative_data_dir
        self.assertEqual((data_dir / ".gitignore").read_text(encoding="utf-8"), GITIGNORE_CONTENT)
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(status.stdout, "")
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", (Path(relative_data_dir) / "tracker.csv").as_posix()],
            cwd=repo,
            check=False,
        )
        self.assertEqual(ignored.returncode, 0)

    def test_default_tracker_add_ignores_fresh_job_search_data(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            run_tracker_add(repo)
            self.assert_ignored_job_search(repo, "job-search")

    def test_custom_data_dir_inside_worktree_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            run_tracker_add(repo, "private/data")
            self.assert_ignored_job_search(repo, "private/data")

    def test_tracked_files_are_reported_and_never_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            data_dir = repo / "job-search"
            data_dir.mkdir()
            tracker_file = data_dir / "tracker.csv"
            original = b"fictional,tracker,data\n"
            tracker_file.write_bytes(original)
            subprocess.run(
                ["git", "add", "job-search/tracker.csv"],
                cwd=repo,
                env=git_env(),
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "Add fictional tracker"],
                cwd=repo,
                env=git_env(),
                check=True,
                capture_output=True,
            )

            result = _common.protect_data_dir(data_dir)
            self.assertFalse(result["protected"])
            self.assertIn("tracker.csv", result["tracked_files"])
            self.assertTrue(any("git rm --cached" in warning for warning in result["warnings"]))
            self.assertEqual(tracker_file.read_bytes(), original)
            tracked = subprocess.run(
                ["git", "ls-files", "--", "job-search/tracker.csv"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(tracked.stdout.splitlines(), ["job-search/tracker.csv"])

    def test_data_dir_that_is_repository_root_is_not_written_to(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)

            result = _common.protect_data_dir(repo)

            self.assertFalse(result["protected"])
            self.assertFalse(result["gitignore_created"])
            self.assertFalse((repo / ".gitignore").exists())
            self.assertTrue(any("data directory is the root" in warning for warning in result["warnings"]))

    def test_existing_gitignore_without_star_is_left_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            data_dir = repo / "private/data"
            data_dir.mkdir(parents=True)
            ignore_file = data_dir / ".gitignore"
            original = b"# Keep the existing local rules.\nnotes.txt\n"
            ignore_file.write_bytes(original)

            result = _common.protect_data_dir(data_dir)

            self.assertFalse(result["protected"])
            self.assertFalse(result["gitignore_created"])
            self.assertEqual(ignore_file.read_bytes(), original)
            self.assertTrue(any("add a line '*'" in warning.lower() for warning in result["warnings"]))

    def test_leading_space_wildcard_is_unprotected_and_left_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            data_dir = repo / "job-search"
            data_dir.mkdir()
            ignore_file = data_dir / ".gitignore"
            original = b" *\n.gitignore\n"
            ignore_file.write_bytes(original)

            result = _common.protect_data_dir(data_dir)

            self.assertFalse(result["protected"])
            self.assertEqual(ignore_file.read_bytes(), original)
            self.assertTrue(any(
                "would not ignore" in warning.lower()
                or "add a line '*'" in warning.lower()
                for warning in result["warnings"]
            ))

    def test_custom_data_dir_with_leading_space_wildcard_is_unprotected(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            data_dir = repo / "private" / "data"
            data_dir.mkdir(parents=True)
            ignore_file = data_dir / ".gitignore"
            original = b" *\n.gitignore\n"
            ignore_file.write_bytes(original)
            env = os.environ.copy()
            env["GJS_DATA_DIR"] = "private/data"

            completed = subprocess.run(
                [sys.executable, str(TRACKER), "protect"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            result = json.loads(completed.stdout)

            self.assertFalse(result["protected"])
            self.assertEqual(ignore_file.read_bytes(), original)
            self.assertTrue(any(
                "would not ignore" in warning.lower()
                or "add a line '*'" in warning.lower()
                for warning in result["warnings"]
            ))

    def test_trailing_spaces_after_wildcard_are_ignored_by_git(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            data_dir = repo / "job-search"
            data_dir.mkdir()
            ignore_file = data_dir / ".gitignore"
            ignore_file.write_bytes(b"*   \n")

            result = _common.protect_data_dir(data_dir)

            self.assertTrue(result["protected"], result["warnings"])
            self.assertEqual(result["warnings"], [])
            ignored = subprocess.run(
                ["git", "check-ignore", "-q", "job-search/tracker.csv"],
                cwd=repo,
                check=False,
            )
            self.assertEqual(ignored.returncode, 0)

    def test_unignored_probe_helper_reports_paths_not_matching_ignore_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            data_dir = repo / "job-search"
            data_dir.mkdir()
            (data_dir / ".gitignore").write_bytes(b"*.md\n")

            unignored = _common._unignored_probes(shutil.which("git"), data_dir)

            self.assertIsNotNone(unignored)
            self.assertIn("tracker.csv", unignored)
            self.assertNotIn("cv.md", unignored)

    def test_gitignore_tracker_exception_is_unprotected_and_left_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            data_dir = repo / "job-search"
            data_dir.mkdir()
            ignore_file = data_dir / ".gitignore"
            original = b"*\n!tracker.csv\n"
            ignore_file.write_bytes(original)

            result = _common.protect_data_dir(data_dir)

            self.assertFalse(result["protected"])
            self.assertEqual(ignore_file.read_bytes(), original)
            self.assertTrue(any(
                str(ignore_file) in warning and "exception" in warning.lower()
                for warning in result["warnings"]
            ))

    def test_gitignore_cv_exception_is_unprotected(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            data_dir = repo / "job-search"
            data_dir.mkdir()
            ignore_file = data_dir / ".gitignore"
            ignore_file.write_text("*\n!cv.md\n", encoding="utf-8")

            result = _common.protect_data_dir(data_dir)

            self.assertFalse(result["protected"])
            self.assertTrue(any(
                str(ignore_file) in warning and "exception" in warning.lower()
                for warning in result["warnings"]
            ))

    def test_escaped_exclamation_rule_is_not_a_negation(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            data_dir = repo / "job-search"
            data_dir.mkdir()
            ignore_file = data_dir / ".gitignore"
            original = b"*\n\\!literal\n"
            ignore_file.write_bytes(original)

            result = _common.protect_data_dir(data_dir)

            self.assertTrue(result["protected"], result["warnings"])
            self.assertEqual(ignore_file.read_bytes(), original)
            self.assertFalse(any("exception" in warning.lower() for warning in result["warnings"]))

    def test_parent_level_negation_matches_effective_git_visibility(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            (repo / ".gitignore").write_text("!job-search/notes.md\n", encoding="utf-8")
            data_dir = repo / "job-search"
            data_dir.mkdir()
            (data_dir / ".gitignore").write_text("*\n", encoding="utf-8")
            notes_file = data_dir / "notes.md"
            notes_file.write_text("Fictional private note.\n", encoding="utf-8")

            effective = subprocess.run(
                [
                    "git",
                    "-C",
                    str(data_dir),
                    "ls-files",
                    "-z",
                    "--others",
                    "--exclude-standard",
                    "--",
                    ".",
                ],
                capture_output=True,
                check=True,
            )
            visible_paths = [
                os.fsdecode(value)
                for value in effective.stdout.split(b"\0")
                if value
            ]
            result = _common.protect_data_dir(data_dir)

            self.assertEqual(result["protected"], not bool(visible_paths))
            if visible_paths:
                self.assertFalse(result["protected"])
                self.assertTrue(any("notes.md" in warning for warning in result["warnings"]))

    def test_custom_data_dir_with_negation_is_unprotected(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            data_dir = repo / "private" / "data"
            data_dir.mkdir(parents=True)
            ignore_file = data_dir / ".gitignore"
            ignore_file.write_text("*\n!cv.md\n", encoding="utf-8")
            env = os.environ.copy()
            env["GJS_DATA_DIR"] = "private/data"

            completed = subprocess.run(
                [sys.executable, str(TRACKER), "protect"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            result = json.loads(completed.stdout)

            self.assertFalse(result["protected"])
            self.assertTrue(any(
                str(ignore_file) in warning and "exception" in warning.lower()
                for warning in result["warnings"]
            ))

    def test_tracker_protect_prints_json_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init_repo(repo)
            env = os.environ.copy()
            env["GJS_DATA_DIR"] = "private/data"

            completed = subprocess.run(
                [sys.executable, str(TRACKER), "protect"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(
                set(result),
                {
                    "path",
                    "in_git_worktree",
                    "worktree",
                    "protected",
                    "gitignore_created",
                    "tracked_files",
                    "warnings",
                    "tracked_check",
                },
            )
            self.assertTrue(result["protected"])
            self.assertTrue(result["gitignore_created"])
            self.assertEqual(result["tracked_check"], "done")


class OutsideGitDataDirTests(unittest.TestCase):
    def test_protection_outside_a_worktree_does_not_create_gitignore(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "private" / "data"

            result = _common.protect_data_dir(data_dir)

            self.assertEqual(
                result,
                {
                    "path": str(data_dir.resolve()),
                    "in_git_worktree": False,
                    "worktree": None,
                    "protected": True,
                    "gitignore_created": False,
                    "tracked_files": [],
                    "warnings": [],
                    "tracked_check": "not_needed",
                },
            )
            self.assertTrue(data_dir.is_dir())
            self.assertFalse((data_dir / ".gitignore").exists())

    def test_protect_if_inside_data_dir_ignores_external_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "job-search"
            external_file = root / "other" / "notes.txt"
            with patch.dict(os.environ, {"GJS_DATA_DIR": str(data_dir)}):
                self.assertIsNone(_common.protect_if_inside_data_dir(external_file))


if __name__ == "__main__":
    unittest.main()
