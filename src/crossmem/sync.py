"""Claude → Gemini one-way memory sync."""

import re
import time
from pathlib import Path

from crossmem.ingest import extract_project_name, parse_markdown_sections

SYNC_START = "<!-- crossmem-sync-start -->"
SYNC_END = "<!-- crossmem-sync-end -->"


def claude_to_gemini_bullet(project: str, section: str, content: str) -> str:
    """Translate a Claude memory section into a Gemini-style bullet.

    Claude format:  structured markdown under a heading
    Gemini format:  flat bullet with project context inline
    """
    # Collapse multi-line content to a single line summary
    lines = [ln.strip() for ln in content.strip().split("\n") if ln.strip()]
    summary = " ".join(lines)
    # Cap at 500 chars — Gemini bullets are concise
    if len(summary) > 500:
        summary = summary[:497] + "..."

    prefix = f"For the '{project}' project"
    if section:
        prefix += f" ({section})"
    return f"- {prefix}: {summary}"


def collect_claude_memories(base_path: Path | None = None) -> list[tuple[str, str, str]]:
    """Read all Claude memory files and return (project, section, content) tuples."""
    if base_path is None:
        base_path = Path.home() / ".claude" / "projects"

    if not base_path.exists():
        return []

    memories = []
    for md_file in sorted(base_path.rglob("*.md")):
        if "memory" not in str(md_file):
            continue

        project = extract_project_name(md_file)
        content = md_file.read_text(encoding="utf-8", errors="replace")
        if not content.strip():
            continue

        sections = parse_markdown_sections(content)
        if not sections:
            memories.append((project, "", content.strip()))
        else:
            for heading, text in sections:
                memories.append((project, heading, text))

    return memories


def filter_for_project(
    memories: list[tuple[str, str, str]], project: str
) -> list[tuple[str, str, str]]:
    """Filter memories for a specific project + cross-project patterns.

    Includes:
    - All memories from the target project
    - Memories from other projects whose section heading appears in 2+ projects
      (these are validated cross-project patterns like "Security", "Temperature Settings")
    """
    # Find sections that appear in multiple projects
    section_projects: dict[str, set[str]] = {}
    for proj, section, _ in memories:
        if section:
            section_projects.setdefault(section, set()).add(proj)

    shared_sections = {
        section for section, projs in section_projects.items() if len(projs) > 1
    }

    return [
        (proj, section, content)
        for proj, section, content in memories
        if proj == project or section in shared_sections
    ]


def build_sync_block(memories: list[tuple[str, str, str]]) -> str:
    """Build the crossmem sync section for GEMINI.md."""
    bullets = [claude_to_gemini_bullet(p, s, c) for p, s, c in memories]
    lines = [
        SYNC_START,
        "## Cross-Project Knowledge (synced by crossmem)",
        *bullets,
        SYNC_END,
    ]
    return "\n".join(lines)


def write_to_gemini(
    sync_block: str, gemini_path: Path | None = None
) -> tuple[int, bool]:
    """Write the sync block to GEMINI.md, preserving Gemini's own content.

    Returns (bullet_count, changed) where changed indicates if the file was modified.
    """
    if gemini_path is None:
        gemini_path = Path.home() / ".gemini" / "GEMINI.md"

    gemini_path.parent.mkdir(parents=True, exist_ok=True)

    if gemini_path.exists():
        existing = gemini_path.read_text(encoding="utf-8", errors="replace")
    else:
        existing = ""

    # Count bullets in the new sync block
    bullet_count = sync_block.count("\n- ")

    # Replace existing sync block or append
    if SYNC_START in existing and SYNC_END in existing:
        pattern = re.escape(SYNC_START) + r".*?" + re.escape(SYNC_END)
        new_content = re.sub(pattern, sync_block, existing, flags=re.DOTALL)
    else:
        separator = "\n\n" if existing.strip() else ""
        new_content = existing.rstrip() + separator + sync_block + "\n"

    changed = new_content != existing
    if changed:
        gemini_path.write_text(new_content, encoding="utf-8")

    return bullet_count, changed


def sync_once(
    claude_path: Path | None = None,
    gemini_path: Path | None = None,
    project: str | None = None,
) -> tuple[int, bool]:
    """Run a single Claude → Gemini sync.

    If project is specified, syncs only that project's memories plus
    cross-project patterns (sections appearing in 2+ projects).

    Returns (bullet_count, changed).
    """
    memories = collect_claude_memories(claude_path)
    if not memories:
        return 0, False
    if project:
        memories = filter_for_project(memories, project)
    if not memories:
        return 0, False
    sync_block = build_sync_block(memories)
    return write_to_gemini(sync_block, gemini_path)


def watch(
    claude_path: Path | None = None,
    gemini_path: Path | None = None,
    interval: int = 30,
    project: str | None = None,
) -> None:
    """Watch Claude memory files and sync to Gemini on changes.

    Polls every `interval` seconds for file modification time changes.
    """
    if claude_path is None:
        claude_path = Path.home() / ".claude" / "projects"

    last_mtimes: dict[str, float] = {}

    print(f"Watching {claude_path} for changes (every {interval}s)")
    print("Press Ctrl+C to stop.\n")

    # Initial sync
    count, changed = sync_once(claude_path, gemini_path, project)
    if changed:
        print(f"Initial sync: {count} memories written to GEMINI.md")
    else:
        print(f"Already in sync ({count} memories)")

    try:
        while True:
            time.sleep(interval)

            # Check for file changes
            current_mtimes: dict[str, float] = {}
            if claude_path.exists():
                for md_file in claude_path.rglob("*.md"):
                    if "memory" in str(md_file):
                        current_mtimes[str(md_file)] = md_file.stat().st_mtime

            if current_mtimes != last_mtimes:
                count, changed = sync_once(claude_path, gemini_path, project)
                if changed:
                    print(f"Synced: {count} memories → GEMINI.md")
                last_mtimes = current_mtimes

    except KeyboardInterrupt:
        print("\nStopped.")
