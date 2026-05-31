#!/usr/bin/env python3
"""Regenerate manifest.yaml from workspaces/*.code-workspace.

Groups repos by workspace (alphabetical by file name) and keeps the folder
order from each workspace. The repos: section is rewritten from scratch, so
reorderings, renames, and removals in workspace files propagate cleanly — no
duplicates and no stale ordering.

Identity model
--------------
A workspace folder is NOT assumed to be its own clone. Repo identity is the
enclosing git work-tree (``git rev-parse --show-toplevel``), so a folder that
points at a *subdirectory* of a repo (e.g. ``devops/bet_bot``) resolves back to
the repo root (``devops``). Multiple folders that live inside the same repo —
or reference it both whole and by subdir — collapse to a single manifest entry.

Everything is keyed by clone **path** (relative to PROJECT_ROOT), never by the
display ``name``. Two unrelated repos may legitimately share a basename (e.g.
``devops`` vs ``royal/devops``); names are a VS Code Explorer concern and only
need to be unique *within a single .code-workspace*.

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
from pathlib import Path, PurePosixPath

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


def git(repo_dir: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_dir), *args],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def repo_toplevel(abs_path: Path) -> Path | None:
    """The enclosing git work-tree root, or None if not inside a repo."""
    if not abs_path.is_dir():
        return None
    top = git(abs_path, "rev-parse", "--show-toplevel")
    return Path(top).resolve() if top else None


def git_origin(repo_dir: Path) -> tuple[str | None, str]:
    url = git(repo_dir, "remote", "get-url", "origin")
    branch = git(repo_dir, "symbolic-ref", "--short", "HEAD") or "main"
    return url, branch


def repo_name_from_url(url: str | None) -> str | None:
    """Canonical repo name = last URL path component, minus a .git suffix."""
    if not url:
        return None
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    tail = tail[:-4] if tail.endswith(".git") else tail
    return tail or None


def rel_from_root(abs_path: Path) -> str | None:
    try:
        return abs_path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return None


def ancestor_match(
    rel: str, by_path: dict[str, dict[str, str]]
) -> dict[str, str] | None:
    """Find a manifest entry whose path is `rel` or an ancestor of it.

    Lets a subdir folder (`devops/bet_bot`) fall back to the repo entry
    (`devops`) recorded in a previous manifest when the clone is missing
    locally (e.g. sync run before bootstrap on a fresh machine).
    """
    p = PurePosixPath(rel)
    for cand in (rel, *(str(a) for a in p.parents if str(a) != ".")):
        if cand in by_path:
            return by_path[cand]
    return None


def parse_existing_manifest() -> tuple[str, dict[str, dict[str, str]]]:
    """Return (header_text_including_repos_line, entries_by_path)."""
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

    raw: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in body_lines:
        if m := re.match(r"\s*-\s*name:\s*(\S+)", line):
            if current:
                raw.append(current)
            current = {"name": m.group(1)}
        elif m := re.match(r"\s*url:\s*(\S+)", line):
            current["url"] = m.group(1)
        elif m := re.match(r"\s*path:\s*(\S+)", line):
            current["path"] = m.group(1)
        elif m := re.match(r"\s*branch:\s*(\S+)", line):
            current["branch"] = m.group(1)
    if current:
        raw.append(current)

    # Key by path — the stable identity. Entries without a path are unusable.
    entries = {e["path"]: e for e in raw if e.get("path")}
    return header, entries


def collect_groups(
    existing: dict[str, dict[str, str]],
) -> tuple[list[tuple[str, list[dict[str, str]]]], list[dict[str, str]], list[str]]:
    """Return (grouped entries, orphan entries, skipped messages).

    Each workspace folder resolves to its enclosing repo. Repos are deduped by
    clone path, so a repo referenced from several workspaces (or via subdirs)
    yields a single entry, grouped under the first workspace that claims it.
    """
    ws_files = sorted(WORKSPACES_DIR.glob("*.code-workspace"))
    groups: list[tuple[str, list[dict[str, str]]]] = []
    seen_paths: dict[str, str] = {}  # repo path -> claiming workspace
    skipped: list[str] = []

    for ws_file in ws_files:
        ws_name = ws_file.stem
        group_entries: list[dict[str, str]] = []
        local_names: dict[str, str] = {}  # name -> path, within THIS file only
        for fname, fpath in parse_workspace(ws_file):
            abs_path = (ws_file.parent / fpath).resolve()
            folder_rel = rel_from_root(abs_path)
            label = fname or Path(fpath).name
            if folder_rel is None:
                skipped.append(f"{label} ({ws_name}): outside PROJECT_ROOT")
                continue

            # Resolve the enclosing repo. The clone target is the repo root,
            # not the (possibly sub-) folder the workspace points at.
            top = repo_toplevel(abs_path)
            live_url: str | None = None
            live_branch = "main"
            if top is not None:
                repo_rel = rel_from_root(top)
                if repo_rel is None:
                    skipped.append(f"{label} ({ws_name}): repo root outside PROJECT_ROOT")
                    continue
                fallback_name = top.name
                live_url, live_branch = git_origin(top)
            else:
                # No work-tree (missing clone or not a repo): fall back to a
                # prior manifest entry at this path or an ancestor of it.
                prior_fb = existing.get(folder_rel) or ancestor_match(folder_rel, existing)
                if not prior_fb:
                    reason = (
                        "dir not found, no manifest fallback"
                        if not abs_path.is_dir()
                        else "not a git repo, no manifest fallback"
                    )
                    skipped.append(f"{label} ({ws_name}): {reason}")
                    continue
                repo_rel = prior_fb["path"]
                fallback_name = prior_fb.get("name") or Path(repo_rel).name

            # url / branch are sticky: once recorded they mean "canonical url +
            # initial clone branch" and survive local drift (a remote not
            # renamed after a GitHub rename, or a checked-out feature branch).
            prior = existing.get(repo_rel)
            url = (prior or {}).get("url") or live_url
            branch = (prior or {}).get("branch") or live_branch or "main"

            if prior and prior.get("url") and live_url and live_url != prior["url"]:
                skipped.append(
                    f"{label} ({ws_name}): url drift — manifest={prior['url']} "
                    f"local={live_url} (manifest kept; update remote or edit manifest)"
                )

            if not url:
                skipped.append(f"{label} ({ws_name}): no origin remote, no manifest fallback")
                continue

            # Manifest name = canonical repo name from its origin URL — stable
            # and decoupled from both the VS Code folder label and the local
            # directory name (on-the-sky/royal_docs.git in royal/docs/ stays
            # "royal_docs").
            name = repo_name_from_url(url) or fallback_name

            # Within one .code-workspace, two folders sharing a display label
            # are ambiguous in the Explorer — flag it (fine across files though).
            if label in local_names and local_names[label] != repo_rel:
                skipped.append(
                    f"{label} ({ws_name}): folder label reused in this workspace "
                    f"for a different repo — give them distinct names"
                )
            local_names[label] = repo_rel

            # Dedupe by repo path: same clone referenced more than once.
            if repo_rel in seen_paths:
                continue

            seen_paths[repo_rel] = ws_name
            group_entries.append(
                {"name": name, "url": url, "path": repo_rel, "branch": branch}
            )

        if group_entries:
            groups.append((ws_name, group_entries))

    # Orphans: entries in old manifest not claimed by any workspace.
    orphans: list[dict[str, str]] = []
    for path, e in existing.items():
        if path in seen_paths:
            continue
        if not all(k in e for k in ("name", "url", "path", "branch")):
            continue
        orphans.append({k: e[k] for k in ("name", "url", "path", "branch")})
    orphans.sort(key=lambda e: e["path"])

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
    new_by_path = {e["path"]: e for _, entries in groups for e in entries}
    for e in orphans:
        new_by_path[e["path"]] = e

    cur_group = {e["path"]: ws for ws, entries in groups for e in entries}
    for e in orphans:
        cur_group[e["path"]] = "orphan"

    added = sorted(p for p in new_by_path if p not in old)
    removed = sorted(p for p in old if p not in new_by_path)
    updated: list[tuple[str, list[str]]] = []
    for p, e in new_by_path.items():
        if p not in old:
            continue
        changes = [
            f"{k}: {old[p].get(k)} -> {e[k]}"
            for k in ("name", "url", "branch")
            if old[p].get(k) != e[k]
        ]
        if changes:
            updated.append((p, changes))

    print()
    print("-- sync summary --")
    if added:
        print(f"Added ({len(added)}):")
        for p in added:
            e = new_by_path[p]
            print(f"  + {e['name']:20s} [{cur_group[p]}]  {p}")
    if updated:
        print(f"Updated ({len(updated)}):")
        for p, ch in updated:
            print(f"  ~ {new_by_path[p]['name']:20s} [{cur_group[p]}]  {p}")
            for c in ch:
                print(f"      {c}")
    if removed:
        print(f"Removed ({len(removed)}):")
        for p in removed:
            print(f"  - {old[p].get('name', '?')}  (was {p})")
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
    # The summary uses ✓/✗/— glyphs; a legacy Windows console (cp950/Big5)
    # raises UnicodeEncodeError on those. Force UTF-8 so it never crashes.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

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
