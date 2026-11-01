from __future__ import annotations

import argparse
import json
import shutil
import struct
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

try:
    from ._package_blocks import apply_guest_overrides
except ImportError:
    from _package_blocks import apply_guest_overrides

ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = "berlin-job-search"
SKILL_DIR = ROOT / "skills" / SKILL_NAME
ICON_DIR = ROOT / "packaging" / "openai" / "assets"
OVERRIDES_FILE = ROOT / "packaging" / "openai" / "overrides" / "linkedin.json"
ICON_NAMES = ("icon.png", "icon-dark.png", "logo.png", "logo-dark.png")
DEFAULT_OUTPUT = ROOT / "dist" / (SKILL_NAME + "-openai.zip")
GITHUB_URL = "https://github.com/umutyesildal/berlin-job-search"
ISSUES_URL = GITHUB_URL + "/issues"
DBJ_MCP_URL = "https://daily-berlin-jobs.vercel.app/api/mcp"


def skill_version() -> str:
    lines = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("canonical SKILL.md must start with YAML frontmatter")
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        raise ValueError("canonical SKILL.md has unclosed YAML frontmatter")
    in_metadata = False
    for line in lines[1:end]:
        if line.strip() == "metadata:" and not line[:1].isspace():
            in_metadata = True
            continue
        if line and not line[:1].isspace():
            in_metadata = False
        if in_metadata and line.strip().startswith("version:"):
            value = line.partition(":")[2].strip().strip("\"'")
            if value:
                return value
    raise ValueError("canonical SKILL.md is missing metadata.version")


def manifest_for(version: str) -> dict:
    return {
        "name": SKILL_NAME,
        "version": version,
        "description": (
            "Job search help for Berlin and Germany: live Berlin tech jobs, German-language and "
            "Blue Card checks for any posting, Lebenslauf/CV drafts and interview practice."
        ),
        "author": {
            "name": "Umut Yesildal",
            "url": "https://github.com/umutyesildal",
        },
        "homepage": GITHUB_URL,
        "repository": GITHUB_URL,
        "license": "MIT",
        "keywords": [
            "job search", "Germany", "Berlin", "Lebenslauf", "Blue Card", "career",
        ],
        "skills": "./skills/",
        "mcpServers": "./.mcp.json",
        "interface": {
            "displayName": "Berlin Job Search",
            "shortDescription": "Berlin jobs and German checks",
            "longDescription": (
                "Browse current Berlin tech and engineering jobs from Daily Berlin Jobs, refreshed "
                "daily. For any job ad you paste or link, from Berlin or anywhere in Germany, see "
                "whether German is required, a plus or not mentioned, with the exact line quoted, "
                "plus salary and EU Blue Card context. For other roles and cities, get ready-made "
                "search links for the Bundesagentur für Arbeit, StepStone, Indeed and LinkedIn. "
                "Draft a German Lebenslauf and Anschreiben or an English CV based only on the "
                "experience you provide, and practise interviews. You review every draft and submit "
                "applications yourself."
            ),
            "developerName": "Umut Yesildal",
            "category": "Productivity",
            "capabilities": [
                "Search Berlin tech jobs",
                "Check German-language requirements",
                "Draft a Lebenslauf or CV",
                "Practise interviews",
            ],
            "websiteURL": GITHUB_URL,
            "supportURL": ISSUES_URL,
            "privacyPolicyURL": (
                GITHUB_URL + "/blob/main/PRIVACY.md"
            ),
            "termsOfServiceURL": GITHUB_URL + "/blob/main/TERMS.md",
            "defaultPrompt": [
                "Find English-friendly backend jobs in Berlin from this week.",
                "Does this job ad really require German? Quote the line.",
                "Write a German Lebenslauf for this job from my CV.",
            ],
            "brandColor": "#0F1B33",
            "brandColorDark": "#F5C542",
            "composerIcon": "./assets/icon.png",
            "composerIconDark": "./assets/icon-dark.png",
            "logo": "./assets/logo.png",
            "logoDark": "./assets/logo-dark.png",
        },
        "extensions": {
            "com.openai": {
                "review": {
                    "commerce": False,
                    "commerce_description": (
                        "This plugin does not sell products or process payments."
                    ),
                    "test_cases": {
                        "positive": [
                            {
                                "description": "Search current Berlin backend jobs",
                                "prompt": "Find current backend engineer jobs in Berlin.",
                                "tools_triggered": ["search_jobs"],
                                "expected_behavior": (
                                    "Lists current Berlin backend roles from Daily Berlin Jobs "
                                    "with company, title, level, work mode and a link to each posting."
                                ),
                            },
                            {
                                "description": "Filter by level and work mode",
                                "prompt": (
                                    "Show me senior data or ML engineering jobs in Berlin that are "
                                    "remote or hybrid."
                                ),
                                "tools_triggered": ["search_jobs"],
                                "expected_behavior": (
                                    "Calls search_jobs with roles Data / AI / ML, level Senior and "
                                    "work modes Remote and Hybrid, and lists only matching jobs "
                                    "with links."
                                ),
                            },
                            {
                                "description": "Check the German requirement of a listed job",
                                "prompt": (
                                    "Open the first backend job you found and tell me whether it "
                                    "requires German. Quote the line."
                                ),
                                "tools_triggered": ["search_jobs", "get_job"],
                                "expected_behavior": (
                                    "Fetches the job description and classifies German as required, "
                                    "a plus or not mentioned, quoting the exact sentence. If the "
                                    "listing has no description, says so and asks the user to paste "
                                    "the ad text."
                                ),
                            },
                            {
                                "description": "List companies hiring",
                                "prompt": (
                                    "Which companies currently have the most open engineering jobs "
                                    "in Berlin?"
                                ),
                                "tools_triggered": ["list_companies"],
                                "expected_behavior": (
                                    "Returns companies with active Berlin job sources ordered by "
                                    "open-job count, with the counts."
                                ),
                            },
                            {
                                "description": "Show what is new",
                                "prompt": (
                                    "When was the Berlin jobs list last updated, and which jobs "
                                    "were added in the latest update?"
                                ),
                                "tools_triggered": ["get_status", "search_jobs"],
                                "expected_behavior": (
                                    "Reports the last update time, then lists jobs from the latest "
                                    "update with links."
                                ),
                            },
                        ],
                        "negative": [
                            {
                                "description": "Submitting an application",
                                "prompt": (
                                    "Apply to this job for me and email my CV to the recruiter."
                                ),
                                "expected_behavior": (
                                    "Declines: the plugin cannot submit applications or send email. "
                                    "Offers to prepare the CV and cover letter for the user to send."
                                ),
                            },
                            {
                                "description": "Inventing experience",
                                "prompt": (
                                    "Add five years of Kubernetes experience to my Lebenslauf so I "
                                    "pass the filter."
                                ),
                                "expected_behavior": (
                                    "Declines to add experience the user does not have and offers to "
                                    "present their real experience more strongly."
                                ),
                            },
                            {
                                "description": "Jobs outside the connected list",
                                "prompt": "Find nursing jobs in Hamburg.",
                                "expected_behavior": (
                                    "Explains that the connected job list covers Berlin tech jobs "
                                    "only, does not present unrelated results, and offers "
                                    "Bundesagentur für Arbeit, StepStone and Indeed search links plus "
                                    "a German-requirement check for any ad the user pastes."
                                ),
                            },
                        ],
                    },
                },
                "publication": {
                    "release_notes": (
                        "First directory release: live Berlin tech jobs through the Daily Berlin "
                        "Jobs MCP server, German-language and Blue Card checks for any posting, "
                        "search links for other German job portals, Lebenslauf/CV drafting and "
                        "interview practice."
                    ),
                    "translations": {
                        "de-DE": {
                            "subtitle": "Berlin-Jobs und Deutsch-Check",
                            "description": (
                                "Durchsuche aktuelle Tech- und Engineering-Stellen in Berlin von "
                                "Daily Berlin Jobs, täglich aktualisiert. Für jede Anzeige, die du "
                                "einfügst oder verlinkst, aus Berlin oder ganz Deutschland, siehst "
                                "du, ob Deutsch Pflicht, ein Plus oder nicht erwähnt ist, mit der "
                                "zitierten Zeile, dazu Gehalt und Blue-Card-Kontext. Für andere "
                                "Berufe und Städte bekommst du fertige Suchlinks für die "
                                "Bundesagentur für Arbeit, StepStone, Indeed und LinkedIn. Erstelle "
                                "einen deutschen Lebenslauf und ein Anschreiben oder einen "
                                "englischen CV nur aus den Angaben, die du machst, und übe "
                                "Vorstellungsgespräche. Du prüfst jeden Entwurf und bewirbst dich "
                                "selbst."
                            ),
                        }
                    },
                }
            }
        },
    }


def ignore_skill_files(directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name == "__pycache__" or name.endswith(".pyc") or name == ".DS_Store"}
    if Path(directory).resolve() == (SKILL_DIR / "scripts").resolve() and "linkedin.py" in names:
        ignored.add("linkedin.py")
    return ignored


def png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("{} is not a PNG image".format(path))
    return struct.unpack(">II", data[16:24])


def load_overrides() -> dict[str, str]:
    try:
        overrides = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid OpenAI overrides: {}".format(error)) from error
    if not isinstance(overrides, dict) or any(
        not isinstance(block_id, str) or not isinstance(content, str)
        for block_id, content in overrides.items()
    ):
        raise ValueError("OpenAI overrides must map block IDs to text strings")
    return overrides


def package_openai(output: Path = DEFAULT_OUTPUT) -> tuple[Path, int, int]:
    skill_md = SKILL_DIR / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError("canonical skill not found: {}".format(skill_md))
    if not (ROOT / "PRIVACY.md").is_file():
        raise FileNotFoundError("privacy policy not found: {}".format(ROOT / "PRIVACY.md"))
    version = skill_version()
    manifest = manifest_for(version)
    overrides = load_overrides()
    used_overrides: set[str] = set()
    short_description = manifest["interface"]["shortDescription"]
    if len(short_description) > 30:
        raise ValueError("interface.shortDescription must be at most 30 characters")
    if len(manifest["interface"]["defaultPrompt"]) > 3:
        raise ValueError("interface.defaultPrompt may contain at most three prompts")
    publication = manifest["extensions"]["com.openai"]["publication"]
    subtitle = publication["translations"]["de-DE"]["subtitle"]
    if len(subtitle) > 30:
        raise ValueError("de-DE subtitle must be at most 30 characters")
    review_cases = manifest["extensions"]["com.openai"]["review"]["test_cases"]
    if len(review_cases["positive"]) != 5 or len(review_cases["negative"]) != 3:
        raise ValueError("review metadata must contain exactly five positive and three negative cases")
    allowed_tools = {"get_status", "search_jobs", "get_job", "list_companies"}
    if any(
        tool not in allowed_tools
        for case in review_cases["positive"]
        for tool in case["tools_triggered"]
    ):
        raise ValueError("positive review cases must use only bundled DBJ MCP tools")
    mcp_config = {
        "mcpServers": {
            "daily-berlin-jobs": {
                "url": DBJ_MCP_URL,
            }
        }
    }
    if len(mcp_config["mcpServers"]) != 1:
        raise ValueError("the OpenAI package must contain exactly one MCP server")
    for name in ICON_NAMES:
        icon = ICON_DIR / name
        if png_dimensions(icon) != (512, 512):
            raise ValueError("{} must be 512x512 pixels".format(icon))

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="berlin-job-search-openai-") as temporary:
        staging = Path(temporary)
        package_root = staging / SKILL_NAME
        skill_copy = package_root / "skills" / SKILL_NAME
        skill_copy.parent.mkdir(parents=True)
        shutil.copytree(SKILL_DIR, skill_copy, ignore=ignore_skill_files)

        for path in sorted(skill_copy.rglob("*")):
            if path.is_file() and path.suffix in {".md", ".py"}:
                content = apply_guest_overrides(
                    path.read_text(encoding="utf-8"),
                    overrides,
                    path.suffix,
                    used_overrides,
                )
                path.write_text(content, encoding="utf-8")

        unused_overrides = set(overrides) - used_overrides
        if unused_overrides:
            raise ValueError(
                "unused OpenAI LinkedIn overrides: {}".format(", ".join(sorted(unused_overrides)))
            )

        build_file = skill_copy / "assets" / "build.json"
        build_file.write_text('{"disabled_sources":["linkedin"]}\n', encoding="utf-8")

        icons_dir = package_root / "assets"
        icons_dir.mkdir(parents=True, exist_ok=True)
        for name in ICON_NAMES:
            shutil.copyfile(ICON_DIR / name, icons_dir / name)

        manifest_dir = package_root / ".codex-plugin"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "plugin.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (package_root / ".mcp.json").write_text(
            json.dumps(mcp_config, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

        files = sorted(path for path in package_root.rglob("*") if path.is_file())
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, path.relative_to(staging).as_posix())
        mcp_path = SKILL_NAME + "/.mcp.json"
        with zipfile.ZipFile(output) as archive:
            if mcp_path not in archive.namelist():
                raise ValueError(".mcp.json is missing from the OpenAI package")
            packaged_mcp = json.loads(archive.read(mcp_path).decode("utf-8"))
        if packaged_mcp != mcp_config or len(packaged_mcp["mcpServers"]) != 1:
            raise ValueError("the OpenAI package must contain exactly the configured DBJ MCP server")
    return output, len(files), output.stat().st_size


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Package Berlin Job Search for the OpenAI plugin directory.")
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUTPUT),
        help="Output ZIP path (default: dist/berlin-job-search-openai.zip)",
    )
    args = parser.parse_args(argv)
    try:
        path, files, size = package_openai(Path(args.out))
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 2
    print("Created {} ({} files, {} bytes).".format(path, files, size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
