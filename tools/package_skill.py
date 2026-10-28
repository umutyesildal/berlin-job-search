from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

from _package_blocks import select_marked_blocks

ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = "berlin-job-search"
SKILL_DIR = ROOT / "skills" / SKILL_NAME
DEFAULT_OUTPUT = ROOT / "dist" / (SKILL_NAME + "-skill.zip")
MAX_UPLOAD_BYTES = 30 * 1024 * 1024


def package_skill(output: Path = DEFAULT_OUTPUT) -> tuple[Path, int, int]:
    skill_md = SKILL_DIR / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError("canonical skill not found: {}".format(skill_md))
    output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(
        path for path in SKILL_DIR.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    if not files:
        raise ValueError("canonical skill directory is empty")

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            relative = Path(SKILL_NAME) / path.relative_to(SKILL_DIR)
            if path.suffix in {".md", ".py"}:
                content = select_marked_blocks(
                    path.read_text(encoding="utf-8"), set(), path.suffix,
                )
                archive.writestr(relative.as_posix(), content)
            else:
                archive.write(path, relative)
    if output.stat().st_size > MAX_UPLOAD_BYTES:
        raise ValueError("skill zip exceeds the general Claude.ai 30 MB file-upload limit")
    return output, len(files), output.stat().st_size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package the complete canonical Berlin Job Search skill.")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="Output zip path (default: dist/berlin-job-search-skill.zip)")
    args = parser.parse_args(argv)
    try:
        path, files, size = package_skill(Path(args.out))
    except (OSError, ValueError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 2
    print("Created {} ({} files, {} bytes).".format(path, files, size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
