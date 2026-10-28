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
    from ._package_blocks import select_marked_blocks
except ImportError:
    from _package_blocks import select_marked_blocks

ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = "berlin-job-search"
SKILL_DIR = ROOT / "skills" / SKILL_NAME
ICON_DIR = ROOT / "packaging" / "openai" / "assets"
ICON_NAMES = ("icon.png", "icon-dark.png", "logo.png", "logo-dark.png")
DEFAULT_OUTPUT = ROOT / "dist" / (SKILL_NAME + "-openai.zip")
DISABLED_SOURCES = {"linkedin"}
GITHUB_URL = "https://github.com/umutyesildal/berlin-job-search"
ISSUES_URL = GITHUB_URL + "/issues"


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
            "Job search for Berlin and the rest of Germany: live jobs, German-language and "
            "Blue Card checks, Lebenslauf/CV drafts and interview prep."
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
        "interface": {
            "displayName": "Berlin Job Search",
            "shortDescription": "Find jobs and check German",
            "longDescription": (
                "Search current jobs in Berlin and across Germany with German and English job "
                "titles. For any posting, see whether German is required, a plus or not mentioned, "
                "with the exact line quoted, plus salary and EU Blue Card context. Draft a German "
                "Lebenslauf and Anschreiben or an English CV from your own CV, practise interviews "
                "and keep track of applications. It never invents experience and never applies or "
                "sends messages for you."
            ),
            "developerName": "Umut Yesildal",
            "category": "Productivity",
            "capabilities": [
                "Search jobs in Germany",
                "Check German-language requirements",
                "Draft a Lebenslauf or CV",
                "Practise interviews",
            ],
            "websiteURL": GITHUB_URL,
            "supportURL": ISSUES_URL,
            "privacyPolicyURL": (
                GITHUB_URL + "/blob/main/PRIVACY.md"
            ),
            "defaultPrompt": [
                "Find English-friendly product manager jobs in Berlin from this week.",
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
                "publication": {
                    "release_notes": (
                        "Initial release: job search for Berlin and Germany, German-language and "
                        "Blue Card checks, Lebenslauf/CV drafting, interview practice."
                    ),
                    "translations": {
                        "de-DE": {
                            "subtitle": "Jobs finden und Deutsch prüfen",
                            "description": (
                                "Durchsuche aktuelle Stellen in Berlin und ganz Deutschland mit "
                                "deutschen und englischen Jobtiteln. Für jede Anzeige siehst du, "
                                "ob Deutsch Pflicht, ein Plus oder nicht erwähnt ist, mit der "
                                "zitierten Zeile, dazu Gehalt und Blue-Card-Kontext. Erstelle "
                                "einen deutschen Lebenslauf und ein Anschreiben oder einen "
                                "englischen CV aus deinem eigenen Lebenslauf, übe "
                                "Vorstellungsgespräche und behalte deine Bewerbungen im Blick. "
                                "Es erfindet keine Erfahrung und bewirbt sich nie für dich."
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


def package_openai(output: Path = DEFAULT_OUTPUT) -> tuple[Path, int, int]:
    skill_md = SKILL_DIR / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError("canonical skill not found: {}".format(skill_md))
    if not (ROOT / "PRIVACY.md").is_file():
        raise FileNotFoundError("privacy policy not found: {}".format(ROOT / "PRIVACY.md"))
    version = skill_version()
    manifest = manifest_for(version)
    short_description = manifest["interface"]["shortDescription"]
    if len(short_description) > 30:
        raise ValueError("interface.shortDescription must be at most 30 characters")
    if len(manifest["interface"]["defaultPrompt"]) > 3:
        raise ValueError("interface.defaultPrompt may contain at most three prompts")
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
                content = select_marked_blocks(
                    path.read_text(encoding="utf-8"), DISABLED_SOURCES, path.suffix,
                )
                path.write_text(content, encoding="utf-8")

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

        files = sorted(path for path in package_root.rglob("*") if path.is_file())
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, path.relative_to(staging).as_posix())
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
