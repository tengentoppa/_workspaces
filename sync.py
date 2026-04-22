#!/usr/bin/env python3
"""Regenerate manifest.yaml from workspaces/*.code-workspace.

Groups repos by workspace (alphabetical by file name) and keeps
the folder order from each workspace. The repos: section of the
manifest is rewritten from scratch, so reorderings, renames, and
removals in workspace files propagate cleanly — no duplicates and
no stale ordering.

Usage:
  python sync.py              # write changes
  python sync.py --dry-run    # preview only
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
MANIFEST = SCRIPT_DIR / "manifest.yaml"
WORKSPACES_DIR = SCRIPT_DIR / "workspaces"


def strip_jsonc(text: str) -> str:
    """Strip // and /* */ comments + trailing commas, preserving strings."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            out.append(text[i : j + 1])
            i = j + 1
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
        else:
            out.append(c)
            i += 1
    return re.sub(r",(\s*[\]}])", r"\1", "".join(out))


def parse_workspace(path: Path) -> list[tuple[str | None, str]]:
    data = json.loads(strip_jsonc(path.read_text(encoding="utf-8")))
    return [(f.get("name"), f["path"]) for f in data.get("folders", []) if f.get("path")]


def git_info(repo_dir: Path) -> tuple[str | None, str]:
    def run(*args: str) -> str | None:
        try:
            return subprocess.check_output(
                ["git", "-C", str(repo_dir), *args],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None

    url = run("remote", "get-url", "origin")
    branch = run("symbolic-ref", "--short", "HEAD") or "main"
    return url, branch


def rel_from_root(abs_path: Path) -> str | None:
    try:
        return abs_path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return None


def parse_existing_manifest() -> tuple[str, dict[str, dict[str, str]]]:
    """Return (header_text_including_repos_line, entries_by_name)."""
    text = MANIFEST.read_text(encoding="utf-8") if MANIFEST.exists() else ""
    lines = text.splitlines()

    repos_idx = next(
        (i for i, line in enumerate(lines) if re.match(r"^repos:\s*$", line)), None
    )
    if repos_idx is None:
        header = text.rstrip() + ("\n\n" if text.strip() else "")
        body_lines: list[str] = []
    else:
        header = "\n".join(lines[:repos_idx]).rstrip() + "\n\n"
        body_lines = lines[repos_idx + 1 :]

    entries: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    for line in body_lines:
        if m := re.match(r"\s*-\s*name:\s*(\S+)", line):
            if current.get("name"):
                entries[current["name"]] = current
            current = {"name": m.group(1)}
        elif m := re.match(r"\s*url:\s*(\S+)", line):
            current["url"] = m.group(1)
        elif m := re.match(r"\s*path:\s*(\S+)", line):
            current["path"] = m.group(1)
        elif m := re.match(r"\s*branch:\s*(\S+)", line):
            current["branch"] = m.group(1)
    if current.get("name"):
        entries[current["name"]] = current

    return header, entries


def collect_groups(
    existing: dict[str, dict[str, str]],
) -> tuple[list[tuple[str, list[dict[str, str]]]], list[dict[str, str]], list[str]]:
    """Return (grouped entries, orphan entries, skipped messages).

    Each folder in each workspace becomes an entry. If the local clone is
    missing but the manifest already has the repo, we preserve the manifest's
    url/branch so bootstrap can still clone it later.
    """
    ws_files = sorted(WORKSPACES_DIR.glob("*.code-workspace"))
    groups: list[tuple[str, list[dict[str, str]]]] = []
    seen_urls: dict[str, str] = {}   # url  -> claiming workspace
    seen_names: dict[str, str] = {}  # name -> claiming workspace
    skipped: list[str] = []

    existing_by_path = {e["path"]: e for e in existing.values() if e.get("path")}

    for ws_file in ws_files:
        ws_name = ws_file.stem
        group_entries: list[dict[str, str]] = []
        for fname, fpath in parse_workspace(ws_file):
            abs_path = (ws_file.parent / fpath).resolve()
            label = fname or Path(fpath).name
            rel = rel_from_root(abs_path)
            if rel is None:
                skipped.append(f"{label} ({ws_name}): outside PROJECT_ROOT")
                continue

            name = fname or abs_path.name

            live_url: str | None = None
            live_branch: str = "main"
            if abs_path.is_dir() and (abs_path / ".git").exists():
                live_url, live_branch = git_info(abs_path)

            # url / branch are sticky: once recorded in the manifest they
            # represent "initial clone branch + canonical url" and should
            # not be clobbered by drift in the local clone (e.g. a remote
            # that was never renamed after a GitHub rename, or a user
            # checking out a feature branch). Only derive from live state
            # for brand-new entries.
            # Match prior entry by path — name alone is ambiguous when two
            # workspaces happen to reuse the same folder label for different
            # repos (e.g. royal/devops vs top-level devops).
            prior = existing_by_path.get(rel)
            url = prior.get("url") if prior else None
            branch = prior.get("branch") if prior else None

            if not url:
                url = live_url
            elif live_url and live_url != url:
                skipped.append(
                    f"{label} ({ws_name}): url drift — manifest={url} "
                    f"local={live_url} (manifest kept; update remote or edit manifest)"
                )
            if not branch:
                branch = live_branch or "main"

            if not url:
                reason = (
                    "dir not found, no manifest fallback"
                    if not abs_path.is_dir()
                    else "not a git repo, no manifest fallback"
                    if not (abs_path / ".git").exists()
                    else "no origin remote, no manifest fallback"
                )
                skipped.append(f"{label} ({ws_name}): {reason}")
                continue

            if url in seen_urls:
                continue  # already claimed by an earlier workspace

            if name in seen_names:
                skipped.append(
                    f"{label} ({ws_name}): name collides with "
                    f"'{name}' from {seen_names[name]} — rename the folder"
                )
                continue

            seen_urls[url] = ws_name
            seen_names[name] = ws_name
            group_entries.append(
                {"name": name, "url": url, "path": rel, "branch": branch}
            )

        if group_entries:
            groups.append((ws_name, group_entries))

    # Orphans: entries in old manifest not claimed by any workspace.
    orphans: list[dict[str, str]] = []
    for n, e in existing.items():
        if n in seen_names:
            continue
        if not all(k in e for k in ("url", "path", "branch")):
            continue
        orphans.append({"name": n, **{k: e[k] for k in ("url", "path", "branch")}})
    orphans.sort(key=lambda e: e["name"])

    return groups, orphans, skipped


def render_manifest(
    header: str,
    groups: list[tuple[str, list[dict[str, str]]]],
    orphans: list[dict[str, str]],
) -> str:
    out = [header.rstrip(), "", "repos:"]
    sections: list[tuple[str, list[dict[str, str]]]] = list(groups)
    if orphans:
        sections.append(("orphan (not in any workspace)", orphans))
    for i, (section_name, entries) in enumerate(sections):
        if i > 0:
            out.append("")
        suffix = "" if section_name.startswith("orphan") else " workspace"
        out.append(f"  # ─── {section_name}{suffix} ───")
        for e in entries:
            out.append(f"  - name: {e['name']}")
            out.append(f"    url: {e['url']}")
            out.append(f"    path: {e['path']}")
            out.append(f"    branch: {e['branch']}")
    return "\n".join(out).rstrip() + "\n"


def diff_report(
    old: dict[str, dict[str, str]],
    groups: list[tuple[str, list[dict[str, str]]]],
    orphans: list[dict[str, str]],
    skipped: list[str],
) -> None:
    new_by_name = {e["name"]: e for _, entries in groups for e in entries}
    for e in orphans:
        new_by_name[e["name"]] = e

    cur_group = {e["name"]: ws for ws, entries in groups for e in entries}
    for e in orphans:
        cur_group[e["name"]] = "orphan"

    added = sorted(n for n in new_by_name if n not in old)
    removed = sorted(n for n in old if n not in new_by_name)
    updated: list[tuple[str, list[str]]] = []
    for n, e in new_by_name.items():
        if n not in old:
            continue
        changes = [
            f"{k}: {old[n].get(k)} -> {e[k]}"
            for k in ("url", "path", "branch")
            if old[n].get(k) != e[k]
        ]
        if changes:
            updated.append((n, changes))

    print()
    print("-- sync summary --")
    if added:
        print(f"Added ({len(added)}):")
        for n in added:
            e = new_by_name[n]
            print(f"  + {n:20s} [{cur_group[n]}]  {e['path']}")
    if updated:
        print(f"Updated ({len(updated)}):")
        for n, ch in updated:
            print(f"  ~ {n:20s} [{cur_group[n]}]")
            for c in ch:
                print(f"      {c}")
    if removed:
        print(f"Removed ({len(removed)}):")
        for n in removed:
            print(f"  - {n}  (was {old[n].get('path', '?')})")
    if orphans:
        print(f"Orphans ({len(orphans)}) — kept but not in any workspace:")
        for e in orphans:
            print(f"  ? {e['name']:20s}  {e['path']}")
    if skipped:
        print(f"Skipped ({len(skipped)}):")
        for s in skipped:
            print(f"  ! {s}")
    if not (added or updated or removed):
        print("No changes.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync manifest.yaml from workspace files.")
    parser.add_argument("--dry-run", action="store_true", help="preview, do not write")
    args = parser.parse_args()

    if not WORKSPACES_DIR.is_dir():
        print(f"✗ workspaces dir not found: {WORKSPACES_DIR}", file=sys.stderr)
        return 1

    header, existing = parse_existing_manifest()
    groups, orphans, skipped = collect_groups(existing)
    new_text = render_manifest(header, groups, orphans)

    diff_report(existing, groups, orphans, skipped)

    current_text = MANIFEST.read_text(encoding="utf-8") if MANIFEST.exists() else ""
    if new_text == current_text:
        print("\nmanifest.yaml already up-to-date.")
        return 0

    if args.dry_run:
        print("\n(dry-run — manifest not modified)")
        return 0

    MANIFEST.write_text(new_text, encoding="utf-8")
    print("\n✓ manifest.yaml rewritten.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
