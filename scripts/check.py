#!/usr/bin/env python3
"""Validate workspace layout and SKILL.md frontmatter for Claude Code and Codex.

A workspace is the repo root or any top-level directory with a skills/ folder
(or the .claude/.agents symlinks that point at one).
"""
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LINKS = (".claude/skills", ".agents/skills")
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)^---[ \t]*$", re.S | re.M)

errors = []


def err(path, msg):
    errors.append(f"{path.relative_to(ROOT)}: {msg}")


def is_workspace(d):
    return any(os.path.lexists(d / p) for p in ("skills", *LINKS))


def check_layout(ws):
    skills = ws / "skills"
    if not skills.is_dir() or skills.is_symlink():
        err(ws, "missing skills/ directory")
    for link in LINKS:
        p = ws / link
        if not p.is_symlink() or os.readlink(p) != "../skills":
            err(ws, f"{link} must be a symlink to ../skills")
    agents, claude = ws / "AGENTS.md", ws / "CLAUDE.md"
    if agents.exists() != claude.exists():
        err(ws, "AGENTS.md and CLAUDE.md must both exist or both be absent")
    elif claude.exists() and "@AGENTS.md" not in claude.read_text():
        err(claude, "must import @AGENTS.md so Claude Code sees the same instructions as Codex")


def check_skill(skill_dir):
    """Return the skill's name if its SKILL.md is valid, else None."""
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        err(skill_dir, "missing SKILL.md")
        return None
    m = FRONTMATTER_RE.match(md.read_text())
    if not m:
        err(md, "must start with YAML frontmatter delimited by --- lines")
        return None
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        err(md, f"invalid YAML frontmatter: {e}")
        return None
    if not isinstance(fm, dict):
        err(md, "frontmatter must be a YAML mapping")
        return None

    ok = True
    name, desc = fm.get("name"), fm.get("description")
    if not isinstance(name, str) or not NAME_RE.match(name) or len(name) > 64:
        err(md, f"name {name!r} must be lowercase-hyphenated and at most 64 characters")
        ok = False
    elif name != skill_dir.name:
        err(md, f"name {name!r} must match its directory name {skill_dir.name!r}")
        ok = False
    if not isinstance(desc, str) or not desc.strip():
        err(md, "description is required")
        ok = False
    elif len(desc) > 1024:
        err(md, f"description is {len(desc)} characters; the limit is 1024")
        ok = False
    elif desc.lstrip().startswith("TODO"):
        err(md, "description is still the scaffold placeholder")
        ok = False
    return name if ok else None


def skill_dirs(ws):
    skills = ws / "skills"
    if not skills.is_dir():
        return []
    return sorted(p for p in skills.iterdir() if p.is_dir())


def main():
    workspaces = [ROOT] + sorted(
        d for d in ROOT.iterdir()
        if d.is_dir() and not d.is_symlink() and not d.name.startswith(".") and is_workspace(d)
    )
    for ws in workspaces:
        check_layout(ws)

    shared = {name for d in skill_dirs(ROOT) if (name := check_skill(d))}
    count = len(shared)
    for ws in workspaces[1:]:
        for d in skill_dirs(ws):
            name = check_skill(d)
            if name is None:
                continue
            count += 1
            if name in shared:
                err(d, f"name {name!r} collides with the shared skill skills/{name}")

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"ok: {len(workspaces) - 1} workspaces, {count} skills")
    return 0


if __name__ == "__main__":
    sys.exit(main())
