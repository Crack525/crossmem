"""Ingest memory files from AI coding tools."""

import re
import subprocess
from pathlib import Path

from crossmem.store import MemoryStore

# Path segments that should never be used as a project name on their own.
# These are common container/workspace directory names that Claude's path
# encoding can make appear as the "last meaningful segment".
_WORKSPACE_DIRS: frozenset[str] = frozenset(
    {
        "documents",
        "personal",
        "workspace",
        "work",
        "code",
        "projects",
        "src",
        "dev",
        "repos",
        "desktop",
        "downloads",
        "library",
        "applications",
        "local",
        "users",
        "home",
    }
)

# Max chars per stored memory chunk. Sections exceeding this are split at
# paragraph boundaries so FTS5 ranking stays accurate and recalls stay focused.
_MAX_SECTION_CHARS = 800

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

    Claude encodes working directory paths as hyphen-separated segments:
    ~/.claude/projects/-Users-foo-Documents-PERSONAL-myproject/memory/MEMORY.md → myproject

    Strategy:
    1. Find the encoded directory (parent of /memory/).
    2. Strip the home-directory prefix (reconstructed from Path.home()).
    3. Walk segments from the END, skipping workspace noise words.
    4. Return the last 1-2 meaningful segments.

    Examples:
    -Users-foo-Documents-PERSONAL-tokenxray  → tokenxray
    -Users-foo-work-backend-api              → backend-api
    -Users-foo-DS-WORKSPACE-my-project       → my-project
    """
    parts = path.parts
    encoded = None
    for i, part in enumerate(parts):
        if part == "memory" and i > 0:
            encoded = parts[i - 1]
            break

    if not encoded:
        return path.parent.name

    # Split on hyphens; the encoded path starts with a leading hyphen so first segment is "".
    raw_segments = [s for s in encoded.split("-") if s]

    # Build home prefix segments from Path.home() so we can strip them.
    # Claude encodes dots as hyphens (e.g. "john.doe" → "john-doe"),
    # so split each home part on "." to match the encoded form.
    home_segments: list[str] = []
    for s in Path.home().parts:
        if s not in ("/", ""):
            home_segments.extend(part.lower() for part in s.split(".") if part)

    # Strip matching home-prefix segments (case-insensitive).
    remaining = [s.lower() for s in raw_segments]
    prefix_len = 0
    for home_seg in home_segments:
        if prefix_len < len(remaining) and remaining[prefix_len] == home_seg:
            prefix_len += 1

    remaining = [s for s in raw_segments[prefix_len:] if s]  # keep original case

    if not remaining:
        return raw_segments[-1] if raw_segments else path.parent.name

    # Walk from END, collect non-workspace segments until we have 1-2 meaningful ones.
    meaningful: list[str] = []
    for seg in reversed(remaining):
        if seg.lower() not in _WORKSPACE_DIRS:
            meaningful.insert(0, seg)
            if len(meaningful) == 2:
                break
        elif meaningful:
            # Stop once we've started collecting and hit a workspace dir.
            break

    if not meaningful:
        # All remaining segments are workspace dirs — fall back to last segment.
        meaningful = [remaining[-1]]

    # Single short abbreviation (≤ 2 chars) — append the next segment for context.
    if len(meaningful) == 1 and len(meaningful[0]) <= 2:
        try:
            idx = remaining.index(meaningful[0])
            if idx + 1 < len(remaining):
                meaningful.append(remaining[idx + 1])
        except ValueError:
            pass

    return "-".join(meaningful).lower()


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_WHY_RE = re.compile(r"\*\*Why:\*\*\s*(.+?)(?=\n\*\*|\Z)", re.DOTALL)
_APPLY_RE = re.compile(r"\*\*How to apply:\*\*\s*(.+?)(?=\n\*\*|\Z)", re.DOTALL)
_GLOBAL_TYPES: frozenset[str] = frozenset({"user", "feedback"})


def parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Extract YAML-like frontmatter from a markdown file.

    Returns (fields_dict, body_without_frontmatter).
    Simple key: value parser — no full YAML dependency.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip()

    body = content[match.end():]
    return fields, body


def _strip_code_blocks(text: str) -> str:
    """Remove fenced code blocks — they inflate chunk size but rarely help text recall."""
    return re.sub(r"```[^`]*```", "", text, flags=re.DOTALL).strip()


def _chunk_section(heading: str, text: str) -> list[tuple[str, str]]:
    """Return (heading, chunk) pairs, splitting at paragraph boundaries when over limit.

    Code blocks are stripped first: they dominate char count but add little
    FTS5 signal since prose around them carries the semantic meaning.
    """
    text = _strip_code_blocks(text)
    if not text or len(text) < 20:
        return []
    if len(text) <= _MAX_SECTION_CHARS:
        return [(heading, text)]

    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip() and len(p.strip()) > 10]
    chunks: list[tuple[str, str]] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        if current and current_len + len(para) > _MAX_SECTION_CHARS:
            chunk_text = "\n\n".join(current)
            if len(chunk_text) > 20:
                chunks.append((heading, chunk_text))
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += len(para) + 2

    if current:
        chunk_text = "\n\n".join(current)
        if len(chunk_text) > 20:
            chunks.append((heading, chunk_text))

    # Fallback: no paragraph breaks found — hard-truncate rather than drop
    return chunks if chunks else [(heading, text[:_MAX_SECTION_CHARS])]


def parse_markdown_sections(content: str) -> list[tuple[str, str]]:
    """Split markdown into (section_heading, section_content) pairs.

    Each section becomes a separate memory for granular search. Sections
    exceeding _MAX_SECTION_CHARS are split at paragraph boundaries and code
    blocks are stripped to keep chunks focused and FTS5 ranking accurate.
    """
    lines = content.split("\n")
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []

    def _emit(heading: str, text: str) -> None:
        if not text or len(text) <= 20:
            return
        chunks = _chunk_section(heading, text)
        if len(chunks) > 1:
            for i, (h, chunk) in enumerate(chunks, 1):
                sections.append((f"{h} [{i}]", chunk))
        else:
            sections.extend(chunks)

    for line in lines:
        if re.match(r"^#{1,3}\s+", line):
            if current_lines:
                _emit(current_heading, "\n".join(current_lines).strip())
            current_heading = re.sub(r"^#{1,3}\s+", "", line).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        _emit(current_heading, "\n".join(current_lines).strip())

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

    return project_dir.name.lower().replace("_", "-") or "unknown"


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
    if not project or not str(project).strip():
        project = derive_project_name(project_dir)

    docs = find_project_docs(project_dir)
    if not docs:
        return 0

    added = 0
    for doc_file in docs:
        content = doc_file.read_text(encoding="utf-8", errors="replace")
        # Strip any crossmem-injected block to avoid feedback loops:
        # install-hook writes markers → ingest reads them → next recall duplicates
        if "<!-- crossmem:" in content:
            import re

            content = re.sub(
                r"<!-- crossmem:auto-injected[^>]*-->.*?<!-- crossmem:end -->",
                "",
                content,
                flags=re.DOTALL,
            ).strip()
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

        # MEMORY.md is the crossmem pointer index when sibling .md files exist.
        # Ingesting it would store pointer lines ("- [Title](file.md) — ...") as
        # memories. Skip it when the directory has other .md files whose content
        # is already ingested by this same loop.
        if md_file.name == "MEMORY.md":
            siblings = [f for f in md_file.parent.glob("*.md") if f.name != "MEMORY.md"]
            if siblings:
                continue

        project = extract_project_name(md_file)
        raw = md_file.read_text(encoding="utf-8", errors="replace")

        if not raw.strip():
            continue

        fm, content = parse_frontmatter(raw)

        mem_type = fm.get("type", "project")
        mem_description = fm.get("description", "")
        mem_scope = "global" if mem_type in _GLOBAL_TYPES else "project"
        # Extract why/how_to_apply from frontmatter or body prose
        mem_why = fm.get("why", "")
        mem_how_to_apply = fm.get("how_to_apply", "")
        if not mem_why:
            m = _WHY_RE.search(content)
            if m:
                mem_why = m.group(1).strip()
        if not mem_how_to_apply:
            m = _APPLY_RE.search(content)
            if m:
                mem_how_to_apply = m.group(1).strip()

        sections = parse_markdown_sections(content)

        if not sections:
            result = store.upsert(
                content=content.strip(),
                source_file=str(md_file),
                project=project,
                section="",
                scope=mem_scope,
                type=mem_type,
                why=mem_why,
                how_to_apply=mem_how_to_apply,
                description=mem_description,
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
                    scope=mem_scope,
                    type=mem_type,
                    why=mem_why,
                    how_to_apply=mem_how_to_apply,
                    description=mem_description,
                )
                if result is not None:
                    added += 1

    return added


def ingest_crossmem_saved(store: MemoryStore, base_path: Path | None = None) -> int:
    """Ingest backing files written by mem_save into the store.

    Reads ~/.crossmem/memories/<project>/<hash>.md. Each file is a single
    memory (no section splitting) — frontmatter carries type/scope/why/how_to_apply.
    """
    if base_path is None:
        base_path = Path.home() / ".crossmem" / "memories"

    if not base_path.exists():
        return 0

    added = 0
    for md_file in sorted(base_path.rglob("*.md")):
        project = md_file.parent.name
        if not project:
            continue

        raw = md_file.read_text(encoding="utf-8", errors="replace")
        if not raw.strip():
            continue

        fm, content = parse_frontmatter(raw)
        content = content.strip()
        if not content:
            continue

        mem_type = fm.get("type", "project")
        mem_description = fm.get("description", "")
        mem_scope = fm.get("scope") or ("global" if mem_type in _GLOBAL_TYPES else "project")
        mem_why = fm.get("why", "")
        mem_how_to_apply = fm.get("how_to_apply", "")
        section = fm.get("name", "") or fm.get("section", "")

        result = store.upsert(
            content=content,
            source_file=str(md_file),
            project=project,
            section=section,
            scope=mem_scope,
            type=mem_type,
            why=mem_why,
            how_to_apply=mem_how_to_apply,
            description=mem_description,
        )
        if result is not None:
            added += 1

    return added
