"""Ingest memory files from AI coding tools."""

import re
import subprocess
from pathlib import Path

from crossmem.store import MemoryStore

# Project doc files to scan during `crossmem init`
PROJECT_DOC_NAMES = {
    "README.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "ARCHITECTURE.md",
    ".github/copilot-instructions.md",
}

# Search in root and docs/ subdirectory
PROJECT_DOC_DIRS = [".", "docs"]


def extract_project_name(path: Path) -> str:
    """Extract project name from Claude Code's path-encoded directory.

    Uses the directory immediately before /memory/ as the project identifier,
    then decodes Claude Code's path encoding (hyphens replace path separators).
    Takes the last 1-2 meaningful segments as the project name.

    ~/.claude/projects/-Users-foo-Documents-myproject/memory/MEMORY.md → myproject
    ~/.claude/projects/-Users-foo-work-backend-api/memory/MEMORY.md → backend-api
    """
    parts = path.parts
    for i, part in enumerate(parts):
        if part == "memory" and i > 0:
            encoded = parts[i - 1]
            # Split on the path-encoding pattern (leading hyphen + segments)
            segments = [s for s in encoded.split("-") if s]
            if not segments:
                return encoded
            # Take last 1-2 segments as project name (most specific)
            # Skip if last segment looks like a hash or is too short
            meaningful = segments[-2:] if len(segments) >= 2 else segments[-1:]
            return "-".join(meaningful)
    return path.parent.name


def parse_markdown_sections(content: str) -> list[tuple[str, str]]:
    """Split markdown into (section_heading, section_content) pairs.

    Each section becomes a separate memory for granular search.
    """
    lines = content.split("\n")
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []

    for line in lines:
        if re.match(r"^#{1,3}\s+", line):
            if current_lines:
                text = "\n".join(current_lines).strip()
                if text and len(text) > 20:
                    sections.append((current_heading, text))
            current_heading = re.sub(r"^#{1,3}\s+", "", line).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        text = "\n".join(current_lines).strip()
        if text and len(text) > 20:
            sections.append((current_heading, text))

    return sections


def extract_gemini_project(text: str) -> str:
    """Extract project name from a Gemini memory bullet.

    Gemini memories often contain "For the 'project-name' project" or
    "project X" patterns. Falls back to "gemini" as the project name.
    """
    match = re.search(r"[Ff]or the ['\"]?([^'\"]+?)['\"]? project", text)
    if match:
        return match.group(1).strip()
    return "gemini"


def ingest_gemini_memory(store: MemoryStore, base_path: Path | None = None) -> int:
    """Ingest Gemini CLI memory file into the store.

    Gemini stores memories as bullet points in ~/.gemini/GEMINI.md.
    Each bullet becomes a separate memory entry.
    Returns the number of new memories added.
    """
    if base_path is None:
        base_path = Path.home() / ".gemini"

    gemini_file = base_path / "GEMINI.md"
    if not gemini_file.exists():
        return 0

    content = gemini_file.read_text(encoding="utf-8", errors="replace")
    if not content.strip():
        return 0

    added = 0
    for line in content.split("\n"):
        line = line.strip()
        if not line.startswith("- "):
            continue
        bullet = line[2:].strip()
        if len(bullet) < 20:
            continue

        project = extract_gemini_project(bullet)
        result = store.add(
            content=bullet,
            source_file=str(gemini_file),
            project=project,
            section="Gemini Added Memories",
        )
        if result is not None:
            added += 1

    return added


def ingest_copilot_memory(store: MemoryStore, base_path: Path | None = None) -> int:
    """Ingest GitHub Copilot memory files into the store.

    Copilot stores memories as markdown files in:
    ~/Library/Application Support/Code/User/globalStorage/
        github.copilot-chat/memory-tool/memories/*.md

    Each file becomes one or more memories (split by headings).
    Returns the number of new memories added.
    """
    if base_path is None:
        base_path = (
            Path.home()
            / "Library"
            / "Application Support"
            / "Code"
            / "User"
            / "globalStorage"
            / "github.copilot-chat"
            / "memory-tool"
            / "memories"
        )

    if not base_path.exists():
        return 0

    added = 0
    for md_file in sorted(base_path.glob("*.md")):
        content = md_file.read_text(encoding="utf-8", errors="replace")
        if not content.strip():
            continue

        # Use filename (without extension) as the section
        file_section = md_file.stem.replace("-", " ").title()
        sections = parse_markdown_sections(content)

        if not sections:
            result = store.upsert(
                content=content.strip(),
                source_file=str(md_file),
                project="copilot",
                section=file_section,
            )
            if result is not None:
                added += 1
        else:
            for heading, text in sections:
                result = store.upsert(
                    content=text,
                    source_file=str(md_file),
                    project="copilot",
                    section=heading or file_section,
                )
                if result is not None:
                    added += 1

    return added


def derive_project_name(project_dir: Path) -> str:
    """Derive a project name from a directory.

    Tries git remote origin first, falls back to directory basename.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            url = result.stdout.strip()
            # Extract repo name from git URL
            # https://github.com/user/repo.git → repo
            # git@github.com:user/repo.git → repo
            name = url.rstrip("/").rsplit("/", 1)[-1]
            name = name.removesuffix(".git")
            if name:
                return name.lower().replace("_", "-")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return project_dir.name.lower().replace("_", "-")


def find_project_docs(project_dir: Path) -> list[Path]:
    """Find project documentation files in a directory."""
    found = []
    for subdir in PROJECT_DOC_DIRS:
        base = project_dir / subdir if subdir != "." else project_dir
        for name in PROJECT_DOC_NAMES:
            candidate = base / name
            if candidate.is_file():
                found.append(candidate)
    return sorted(found)


def has_project_docs(project_dir: Path) -> bool:
    """Check if a directory has any project documentation files."""
    return len(find_project_docs(project_dir)) > 0


def ingest_project_docs(
    store: MemoryStore,
    project_dir: Path,
    project: str | None = None,
) -> int:
    """Ingest project documentation files into the store.

    Scans for README.md, CLAUDE.md, CONTRIBUTING.md, ARCHITECTURE.md,
    and .github/copilot-instructions.md in the project root and docs/.

    Re-runnable: uses content-hash dedup so unchanged content is skipped.
    """
    if project is None:
        project = derive_project_name(project_dir)

    docs = find_project_docs(project_dir)
    if not docs:
        return 0

    added = 0
    for doc_file in docs:
        content = doc_file.read_text(encoding="utf-8", errors="replace")
        if not content.strip():
            continue

        source = f"init:{doc_file.relative_to(project_dir)}"
        sections = parse_markdown_sections(content)

        if not sections:
            result = store.upsert(
                content=content.strip(),
                source_file=source,
                project=project,
                section=doc_file.stem,
            )
            if result is not None:
                added += 1
        else:
            for heading, text in sections:
                result = store.upsert(
                    content=text,
                    source_file=source,
                    project=project,
                    section=heading or doc_file.stem,
                )
                if result is not None:
                    added += 1

    return added


def ingest_claude_memory(store: MemoryStore, base_path: Path | None = None) -> int:
    """Ingest all Claude Code memory files into the store.

    Returns the number of new memories added.
    """
    if base_path is None:
        base_path = Path.home() / ".claude" / "projects"

    if not base_path.exists():
        return 0

    added = 0
    for md_file in sorted(base_path.rglob("*.md")):
        if "memory" not in str(md_file):
            continue

        project = extract_project_name(md_file)
        content = md_file.read_text(encoding="utf-8", errors="replace")

        if not content.strip():
            continue

        sections = parse_markdown_sections(content)

        if not sections:
            # No sections found — store the whole file as one memory
            result = store.upsert(
                content=content.strip(),
                source_file=str(md_file),
                project=project,
                section="",
            )
            if result is not None:
                added += 1
        else:
            for heading, text in sections:
                result = store.upsert(
                    content=text,
                    source_file=str(md_file),
                    project=project,
                    section=heading,
                )
                if result is not None:
                    added += 1

    return added
