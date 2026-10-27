from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
NAME = "berlin-job-search"
SKILL = ROOT / "skills" / NAME
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RESOURCE_RE = re.compile(r"(?<![\w.-])(?:references|assets|scripts)/[A-Za-z0-9_.@/-]+")
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
MANIFESTS = (
    ROOT / ".claude-plugin" / "plugin.json",
    ROOT / ".claude-plugin" / "marketplace.json",
    ROOT / ".codex-plugin" / "plugin.json",
    ROOT / ".agents" / "plugins" / "marketplace.json",
    ROOT / ".mcp.json",
)
VERSIONED = set(MANIFESTS[:3])
LOCAL_MARKERS = (bytes((47, 116, 109, 112, 47)), bytes((47, 85, 115, 101, 114, 115, 47)))

def frontmatter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("{} must start with YAML frontmatter".format(path.relative_to(ROOT)))
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("{} has unclosed frontmatter".format(path.relative_to(ROOT)))
    fields: dict[str, str] = {}
    parent = ""
    for line in lines[1:end]:
        item = line.strip()
        key, separator, value = item.partition(":")
        if not item or item.startswith("#") or not separator:
            continue
        if not line[:1].isspace():
            parent = key
            if value.strip():
                fields[key] = value.strip().strip("\"'")
        elif parent == "metadata":
            fields[parent + "." + key] = value.strip().strip("\"'")
    return fields, text

def validate_skill(version: str) -> list[str]:
    path = SKILL / "SKILL.md"
    if not path.is_file():
        return ["missing canonical skill: {}".format(path.relative_to(ROOT))]
    try:
        fields, text = frontmatter(path)
    except (OSError, UnicodeError, ValueError) as error:
        return [str(error)]
    name, description = fields.get("name", ""), fields.get("description", "")
    errors = []
    if name != NAME or not NAME_RE.fullmatch(name):
        errors.append("SKILL.md name must match the berlin-job-search directory")
    if not description or len(description) > 1024:
        errors.append("SKILL.md description must be present and at most 1024 characters")
    if len(text.splitlines()) > 500:
        errors.append("SKILL.md exceeds 500 lines")
    if fields.get("metadata.version") != version:
        errors.append("SKILL.md metadata.version must match its manifests")
    for reference in set(RESOURCE_RE.findall(text)):
        path = SKILL / reference.rstrip(".,;:!?)]}`\"'")
        if not path.exists():
            errors.append("SKILL.md references missing path: {}".format(reference))
    return errors

def validate_manifests(version: str) -> list[str]:
    errors = []
    for path in MANIFESTS:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            errors.append("{}: invalid or missing JSON ({})".format(path.relative_to(ROOT), error))
            continue
        if path in VERSIONED and manifest.get("version") != version:
            errors.append("{}: version must match SKILL.md".format(path.relative_to(ROOT)))
        for plugin in manifest.get("plugins", []):
            if "version" in plugin and plugin["version"] != version:
                errors.append("{}: plugin version must match SKILL.md".format(path.relative_to(ROOT)))
        if path.name == ".mcp.json":
            servers = manifest.get("mcpServers")
            if not isinstance(servers, dict) or not servers:
                errors.append(".mcp.json: mcpServers must be a non-empty object")
            else:
                for name, server in servers.items():
                    try:
                        url = urlsplit(str(server.get("url", ""))) if isinstance(server, dict) else None
                    except ValueError:
                        url = None
                    if not isinstance(server, dict) or server.get("type") not in {"http", "sse", "ws"} or not url or url.scheme != "https" or not url.netloc:
                        errors.append(".mcp.json: {} must use http, sse, or ws and an absolute https URL".format(name))
    return errors

def validate_symlinks() -> list[str]:
    target = SKILL.resolve()
    links = (".agents/skills/berlin-job-search", ".claude/skills/berlin-job-search")
    return [
        "{} must resolve to skills/berlin-job-search".format(name)
        for name in links
        if not (ROOT / name).is_symlink() or (ROOT / name).resolve() != target
    ]

def validate_local_paths() -> list[str]:
    excluded = {".git", "dist", "__pycache__"}
    return [
        "{} contains a local machine path".format(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if path.is_file() and not excluded.intersection(path.relative_to(ROOT).parts)
        and any(marker in path.read_bytes() for marker in LOCAL_MARKERS)
    ]

def validate_readme_links() -> list[str]:
    errors = []
    for filename in ("README.md", "README.de.md"):
        readme = ROOT / filename
        if not readme.is_file():
            if filename == "README.md":
                errors.append("{} is missing".format(filename))
            continue
        in_fence = False
        for number, line in enumerate(readme.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith(("```", "~~~")):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for match in LINK_RE.finditer(line):
                target = match.group(1).strip()
                target = target[1:target.index(">" )] if target.startswith("<") and ">" in target else target.split(None, 1)[0]
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith("/"):
                    continue
                resolved = (readme.parent / unquote(parsed.path)).resolve()
                try:
                    resolved.relative_to(ROOT)
                except ValueError:
                    errors.append("{}:{} link escapes repository: {}".format(filename, number, target))
                    continue
                if not resolved.exists():
                    errors.append("{}:{} broken relative link: {}".format(filename, number, target))
    return errors

def main() -> int:
    try:
        fields, _ = frontmatter(SKILL / "SKILL.md")
        version = fields.get("metadata.version", "")
    except (OSError, UnicodeError, ValueError) as error:
        print("ERROR: {}".format(error), file=sys.stderr)
        return 1
    errors = validate_skill(version) + validate_manifests(version) + validate_symlinks()
    errors += validate_local_paths() + validate_readme_links()
    if errors:
        for error in errors:
            print("ERROR: {}".format(error), file=sys.stderr)
        print("Skill validation failed with {} error(s).".format(len(errors)), file=sys.stderr)
        return 1
    print("Skill validation passed for version {}.".format(version))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
