"""MCP server for crossmem — exposes memory search to AI coding tools."""

import os
import re
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from crossmem.ingest import (
    derive_project_name,
    has_project_docs,
    ingest_claude_memory,
    ingest_copilot_memory,
    ingest_gemini_memory,
    ingest_project_docs,
)
from crossmem.stopwords import CLOSED_CLASS
from crossmem.store import MemoryStore

mcp = FastMCP("crossmem")

_ingested: bool = False
_ingest_lock = threading.Lock()


def _freshness(last_verified: str | None) -> str:
    """Return a short freshness label, e.g. '[verified: 2026-05-02]' or '[unverified]'."""
    if not last_verified:
        return "[unverified]"
    return f"[verified: {last_verified[:10]}]"


# Matches file paths in memory content: e.g. src/foo/bar.py, tests/test_x.py, ~/.claude/...
_FILE_REF_RE = re.compile(r"(?:src|tests?|~)/[\w./\-]+\.(?:py|md|toml|json|yaml|yml|sh)")


def _stale_check(source_file: str | None, content: str, cwd: str | None) -> str | None:
    """Return a stale label if the source file or content-referenced files no longer exist.

    Checks two things:
    1. source_file — the file this memory was ingested from (absolute or relative to cwd)
    2. File path patterns in content (src/..., tests/..., ~/.../...) checked against cwd

    Returns '[stale: <reason>]' or None.
    """
    base = Path(cwd) if cwd else None

    if source_file:
        p = Path(source_file).expanduser()
        if not p.is_absolute() and base:
            p = base / p
        if not p.exists():
            return "[stale: source file not found]"

    if base:
        for match in _FILE_REF_RE.findall(content):
            p = Path(match).expanduser()
            if not p.is_absolute():
                p = base / p
            if not p.exists():
                return f"[stale: {match} not found]"

    return None


def _status(mem, cwd: str | None = None) -> str:
    """Return [stale: ...] if detectable, else the freshness label, plus save date."""
    stale = _stale_check(mem.source_file, mem.content, cwd)
    date = mem.created_at[:10] if getattr(mem, "created_at", None) else None
    date_tag = f" [saved: {date}]" if date else ""
    if stale:
        return stale + date_tag
    return _freshness(mem.last_verified) + date_tag


_SESSION_FOOTER = (
    "_During this session: call mem_save() for any decision, gotcha, or pattern worth keeping. "
    "Call mem_update(id=...) to correct an existing memory rather than saving a duplicate._"
)


def _format_memory_line(mem, cwd: str | None = None) -> list[str]:
    """Format a memory as 1-2 lines for recall output.

    Feedback/user memories with how_to_apply get a second line so the
    behavioral instruction is visually distinct and harder to miss.
    """
    section = f" / {mem.section}" if mem.section else ""
    type_tag = f" [{mem.type}]" if getattr(mem, "type", "project") != "project" else ""
    line = (
        f"- (id: {mem.id}) {_status(mem, cwd)}"
        f" **{mem.project}{section}**{type_tag}: {mem.snippet}"
    )
    lines = [line]
    how_to_apply = getattr(mem, "how_to_apply", "")
    if how_to_apply and getattr(mem, "type", "") in ("feedback", "user"):
        lines.append(f"  → **Apply:** {how_to_apply}")
    return lines


def _dedup_memories(memories: list) -> list:
    """Return memories with duplicate content_hash entries removed (keeps first seen)."""
    seen: set[str] = set()
    out = []
    for mem in memories:
        h = getattr(mem, "content_hash", None)
        if h and h in seen:
            continue
        if h:
            seen.add(h)
        out.append(mem)
    return out


def _dedup_search_results(results: list) -> list:
    """Return search results with duplicate content_hash entries removed (keeps first seen)."""
    seen: set[str] = set()
    out = []
    for r in results:
        h = getattr(r.memory, "content_hash", None)
        if h and h in seen:
            continue
        if h:
            seen.add(h)
        out.append(r)
    return out


def _injection_summary(memories: list) -> str:
    """One-line italicized header summarising injected memories for the LLM.

    Format: _Context loaded: N memories — 3 feedback [global], 2 user [global],
            3 project [tokenxray] · oldest: 2026-04-15_
    """
    from collections import Counter
    from datetime import datetime

    if not memories:
        return ""

    type_scope: Counter = Counter()
    projects: dict[str, str] = {}  # type_scope_key → project label
    dates: list[datetime] = []

    for mem in memories:
        mem_type = getattr(mem, "type", "project") or "project"
        mem_scope = getattr(mem, "scope", "project") or "project"
        mem_project = getattr(mem, "project", "") or ""
        key = f"{mem_type}|{mem_scope}|{mem_project}"
        type_scope[key] += 1
        if mem_project:
            projects[key] = mem_project

        lv = getattr(mem, "last_verified", None)
        if lv:
            try:
                dates.append(datetime.fromisoformat(lv))
            except ValueError:
                pass

    parts: list[str] = []
    for key, count in type_scope.most_common():
        mem_type, mem_scope, mem_project = key.split("|", 2)
        label = f"[{mem_project}]" if mem_scope == "project" and mem_project else "[global]"
        parts.append(f"{count} {mem_type} {label}")

    total = len(memories)
    summary = f"_Context loaded: {total} memor{'y' if total == 1 else 'ies'}"
    if parts:
        summary += " — " + ", ".join(parts)
    if dates:
        oldest = min(dates).strftime("%Y-%m-%d")
        summary += f" · oldest: {oldest}"
    summary += "_"
    return summary


_MCP_RECALL_LIMIT: int = 10
# Hard limit for mem_save content. Typed memories (section != "") get 2000 chars;
# untyped root-level saves keep the tighter 1000-char limit.
_MEM_SAVE_MAX_CHARS: int = 1000
_MEM_SAVE_MAX_CHARS_TYPED: int = 2000

_GLOBAL_TYPES: frozenset[str] = frozenset({"user", "feedback"})
_VALID_TYPES: frozenset[str] = frozenset({"user", "feedback", "project", "reference"})


def get_store() -> MemoryStore:
    """Return a fresh MemoryStore connection.

    Caller MUST call store.close() when done to release the DB lock.
    Auto-ingest runs once on the first call per process, guarded by a lock
    so concurrent MCP tool calls cannot trigger duplicate ingestion.
    """
    global _ingested
    store = MemoryStore()
    if not _ingested:
        with _ingest_lock:
            if not _ingested:
                ingest_claude_memory(store)
                ingest_gemini_memory(store)
                ingest_copilot_memory(store)
                _ingested = True
    return store


@mcp.tool()
def mem_search(query: str, project: str | None = None, limit: int = 10) -> str:
    """Search across all project memories.

    Use this to find patterns, decisions, and solutions from past projects.
    Multi-word queries use AND logic. Use quoted phrases for exact matches.

    Args:
        query: Search terms (e.g. "credential masking", "docker sidecar")
        project: Optional project name to filter results
        limit: Max number of results (default 10)
    """
    store = get_store()
    try:
        project = project.strip() if project else None
        if not project:
            project = None
        results = _dedup_search_results(store.search_auto(query, limit=limit, project=project))

        if not results:
            return f'No results for "{query}"'

        lines = [f'Found {len(results)} results for "{query}":\n']
        for i, result in enumerate(results, 1):
            mem = result.memory
            lines.append(
                f"[{i}] {mem.project} / {mem.section or '(root)'} (id: {mem.id})"
                f" {_freshness(mem.last_verified)}"
                f" — to edit: mem_update(memory_id={mem.id}, content=...)"
            )
            lines.append(f"    Source: {mem.source_file.split('/')[-1]}")
            lines.append(f"    {mem.snippet}")
            lines.append("")

        return "\n".join(lines)
    finally:
        store.close()


def resolve_project(cwd: str, known_projects: list[str]) -> str | None:
    """Map a working directory path to a known project name.

    Matching strategy:
    1. Exact match — a path segment equals a project name
    2. Suffix match — project name appears at the end of a path segment
    3. Fuzzy segment match — last 1-3 path segments combined with hyphens
       match a project name (mirrors Claude's path encoding)
    """
    if not isinstance(cwd, str):
        return None
    path = Path(cwd)
    segments = [s.lower().replace("_", "-") for s in path.parts if s != "/"]
    projects_lower = {p.lower().replace("_", "-"): p for p in known_projects}

    # 1. Exact match on any path segment (prefer rightmost / most specific)
    for seg in reversed(segments):
        if seg in projects_lower:
            return projects_lower[seg]

    # 2. Suffix match — e.g. cwd "my-backend-api" matches project "backend-api"
    # Require a hyphen boundary to prevent "my-app" matching project "app".
    for seg in reversed(segments):
        for plower, poriginal in projects_lower.items():
            if seg == plower or seg.endswith("-" + plower):
                return poriginal

    # 3. Fuzzy: combine last N segments with hyphens (how Claude encodes paths)
    for n in range(2, min(4, len(segments) + 1)):
        combo = "-".join(segments[-n:])
        if combo in projects_lower:
            return projects_lower[combo]

    return None


@mcp.tool()
def mem_recall(
    project: str | None = None,
    cwd: str | None = None,
    query: str | None = None,
) -> str:
    """Recall relevant memories for a project at session start.

    Returns the project's own memories plus cross-project patterns
    (knowledge validated across multiple projects). Call this at the
    beginning of a coding session to load context.

    If no project is given, auto-detects from the working directory
    by matching path segments against known project names.

    Args:
        project: Project name to recall memories for (auto-detected if omitted)
        cwd: Working directory path for auto-detection (defaults to os.getcwd())
        query: Pass this whenever you know what the session is about — a short
               phrase like "auth setup" or "deploy process" returns only the
               relevant memories and uses fewer tokens. Omit only for a true
               cold-start dump with no intent yet.
    """
    store = get_store()
    try:
        cwd = cwd or os.getcwd()
        project_dir = Path(cwd)
        project = project.strip() if project else None
        if not project:
            project = None

        if not project:
            known = store.list_projects()
            project = resolve_project(cwd, known)
            if not project:
                if has_project_docs(project_dir):
                    project = derive_project_name(project_dir)
                    ingest_project_docs(store, project_dir, project=project)
                else:
                    global_mems = store.get_global_memories(limit=10)
                    if global_mems:
                        lines = [
                            _injection_summary(global_mems),
                            "",
                            f'Could not detect project from "{cwd}".',
                            f"Known projects: {', '.join(known)}",
                            "Pass an explicit project name to mem_recall(project=...).\n",
                            f"## Cross-project patterns ({len(global_mems)} available):\n",
                        ]
                        for mem in global_mems:
                            lines.extend(_format_memory_line(mem, cwd))
                        lines.append("")
                        lines.append(_SESSION_FOOTER)
                        return "\n".join(lines)
                    return (
                        f'Could not detect project from "{cwd}".\n'
                        f"Known projects: {', '.join(known)}\n"
                        "Pass an explicit project name to mem_recall(project=...)."
                    )

        if query:
            results = store.search_expanded(query, limit=_MCP_RECALL_LIMIT, project=project)
            if not results:
                # Fallback to full tier-sorted dump with notice
                project_memories = store.get_by_project(project)
                if not project_memories and has_project_docs(project_dir):
                    ingest_project_docs(store, project_dir, project=project)
                    project_memories = store.get_by_project(project)
                shared_memories = store.get_global_memories(limit=20)
                all_mems = list(project_memories) + list(shared_memories)
                summary_line = _injection_summary(all_mems)
                lines = [summary_line, ""] if summary_line else []
                lines.append(f'_(No scoped results for "{query}". Showing all {project} memories.)_\n')
                if project_memories:
                    lines.append(f"## {project} memories ({len(project_memories)}):\n")
                    for mem in project_memories:
                        lines.extend(_format_memory_line(mem, cwd))
                    lines.append("")
                if shared_memories:
                    lines.append(
                        f"## Cross-project patterns ({len(shared_memories)} from other projects):\n"
                    )
                    for mem in shared_memories:
                        lines.extend(_format_memory_line(mem, cwd))
                    lines.append("")
                lines.append(_SESSION_FOOTER)
                return "\n".join(lines)

            seen_ids = {r.memory.id for r in results}
            global_mems = store.get_global_memories(query=query, limit=5)
            global_mems = [m for m in global_mems if m.id not in seen_ids]

            all_mems = [r.memory for r in results] + global_mems
            summary_line = _injection_summary(all_mems)
            lines = [summary_line, ""] if summary_line else []

            lines.append(f"## {project} memories (scoped: {query!r}):\n")
            for result in results:
                lines.extend(_format_memory_line(result.memory, cwd))
            lines.append("")

            if global_mems:
                lines.append(f"## Cross-project patterns (scoped: {query!r}):\n")
                for mem in global_mems:
                    lines.extend(_format_memory_line(mem, cwd))
                lines.append("")

            lines.append(_SESSION_FOOTER)
            return "\n".join(lines)

        # Get project-specific memories from the store
        project_memories = _dedup_memories(store.get_by_project(project))

        # Auto-init if project exists but has no memories yet
        if not project_memories and has_project_docs(project_dir):
            ingest_project_docs(store, project_dir, project=project)
            project_memories = _dedup_memories(store.get_by_project(project))

        # Get globally scoped memories (cross-project patterns)
        seen_project_hashes = {m.content_hash for m in project_memories if m.content_hash}
        shared_memories = [
            m
            for m in store.get_global_memories(limit=20)
            if m.content_hash not in seen_project_hashes
        ]

        all_mems = list(project_memories) + list(shared_memories)
        summary_line = _injection_summary(all_mems)
        lines = [summary_line, ""] if summary_line else []

        if project_memories:
            lines.append(f"## {project} memories ({len(project_memories)}):\n")
            for mem in project_memories:
                lines.extend(_format_memory_line(mem, cwd))
            lines.append("")

        if shared_memories:
            lines.append(
                f"## Cross-project patterns ({len(shared_memories)} from other projects):\n"
            )
            for mem in shared_memories:
                lines.extend(_format_memory_line(mem, cwd))
            lines.append("")

        if not lines:
            return f'No memories found for project "{project}". Run mem_ingest() first.'

        lines.append(_SESSION_FOOTER)
        return "\n".join(lines)
    finally:
        store.close()


@mcp.tool()
def mem_save(
    content: str,
    section: str = "",
    project: str | None = None,
    cwd: str | None = None,
    scope: str | None = None,
    type: str = "project",
    why: str = "",
    how_to_apply: str = "",
    description: str = "",
) -> str:
    """Save a memory during a coding session.

    Use this when you discover something worth remembering:
    patterns, decisions, gotchas, architecture notes, debugging
    insights, or any knowledge that would help future sessions.

    The memory is immediately searchable via mem_search.

    Args:
        content: The memory content to save (be specific and actionable)
        section: Category heading (e.g. "Security", "Architecture", "Gotchas")
        project: Project name (auto-detected from cwd if omitted)
        cwd: Working directory for auto-detection (defaults to os.getcwd())
        scope: 'project' or 'global'. Auto-derived from type if omitted:
               user/feedback → global, project/reference → project.
        type: Memory type — 'user' | 'feedback' | 'project' | 'reference'.
              Drives auto-scope and recall priority. Default: 'project'.
        why: The reason this matters — a hidden constraint, past incident, or
             strong preference that justifies this memory existing.
        how_to_apply: Concrete guidance on when/how to use this memory.
                      Injected prominently in recall for feedback/user types.
        description: One-line summary for index display.
    """
    if not content or not content.strip():
        return "Content cannot be empty."
    if len(content.strip()) < 10:
        n = len(content.strip())
        return f"Content too short ({n} chars, min 10). Be specific and actionable."
    if type not in _VALID_TYPES:
        return f"Invalid type '{type}'. Use: user, feedback, project, reference."

    # Auto-derive scope from type if not explicitly provided
    if scope is None:
        scope = "global" if type in _GLOBAL_TYPES else "project"
    elif scope not in ("project", "global"):
        return f"Invalid scope '{scope}'. Use 'project' or 'global'."

    # Typed memories get a more generous limit; raw root-level saves stay tight
    char_limit = (
        _MEM_SAVE_MAX_CHARS_TYPED if (section or type != "project") else _MEM_SAVE_MAX_CHARS
    )
    if len(content) > char_limit:
        return (
            f"Content too long ({len(content)} chars, max {char_limit}). "
            "Distill to one actionable sentence or short paragraph. "
            "Good: 'Deploy with uv publish --token $TOKEN; token in 1Password.' "
            "Bad: pasting README sections, code blocks, or full file contents."
        )

    store = get_store()
    try:
        project = project.strip() if project else None
        if not project:
            cwd = cwd or os.getcwd()
            known = store.list_projects()
            project = resolve_project(cwd, known)
            if not project:
                # Derive project name from last cwd segment
                project = Path(cwd).name.lower().replace("_", "-") or "unknown"

        result = store.add(
            content=content,
            source_file="mcp:mem_save",
            project=project,
            section=section,
            scope=scope,
            type=type,
            why=why,
            how_to_apply=how_to_apply,
            description=description,
        )

        if result is None:
            return f"Memory already exists for project '{project}'."

        msg = f"Saved to '{project}'" + (f" / {section}" if section else "") + f" (id: {result})"

        # Surface similar memories so the agent can update instead of accumulating duplicates.
        # Use OR mode with stopword-filtered signal words for broader near-duplicate detection.
        signal_words = [
            w
            for raw in content.lower().split()
            if (w := re.sub(r"[^\w-]", "", raw)) and w not in CLOSED_CLASS and len(w) > 2
        ]
        probe = " ".join(signal_words[:8])
        similar = store.search(probe, limit=3, project=project, or_mode=True) if probe else []
        similar = [r for r in similar if r.memory.id != result]
        if similar:
            hints = ", ".join(f"id:{r.memory.id} — {r.memory.snippet[:60]}" for r in similar[:2])
            msg += f"\nSimilar memories exist: {hints}. Use mem_update(id=...) if this overlaps."

        return msg
    finally:
        store.close()


@mcp.tool()
def mem_get(memory_id: int) -> str:
    """Get the full content of a memory by ID.

    Use this after mem_search or mem_recall to read a memory's complete
    content (search results are truncated to 200 chars).

    Args:
        memory_id: The ID of the memory to retrieve (shown in search results)
    """
    store = get_store()
    try:
        mem = store.get(memory_id)
        if not mem:
            return f"Memory {memory_id} not found."

        section = f" / {mem.section}" if mem.section else ""
        return f"## {mem.project}{section} (id: {mem.id})\n\n{mem.content}"
    finally:
        store.close()


@mcp.tool()
def mem_update(
    memory_id: int,
    content: str,
    section: str | None = None,
    project: str | None = None,
    scope: str | None = None,
) -> str:
    """Update an existing memory in place, preserving its ID.

    Use this instead of delete + re-save when correcting or evolving
    a memory. The ID stays the same so references don't break.

    Args:
        memory_id: The ID of the memory to update (find via mem_search)
        content: The new content (replaces the old content entirely)
        section: New section/category (keeps current if omitted)
        project: New project name (keeps current if omitted)
        scope: 'project' or 'global' — use to promote/demote scope (keeps current if omitted)
    """
    if not content or not content.strip():
        return "Content cannot be empty."
    if len(content.strip()) < 10:
        n = len(content.strip())
        return f"Content too short ({n} chars, min 10). Be specific and actionable."
    if len(content) > _MEM_SAVE_MAX_CHARS_TYPED:
        return (
            f"Content too long ({len(content)} chars, max {_MEM_SAVE_MAX_CHARS_TYPED}). "
            "Distill to one actionable sentence or short paragraph."
        )
    if scope is not None and scope not in ("project", "global"):
        return f"Invalid scope '{scope}'. Use 'project' or 'global'."

    store = get_store()
    try:
        mem = store.get(memory_id)
        if not mem:
            return f"Memory {memory_id} not found."

        updated = store.update(
            memory_id=memory_id,
            content=content,
            section=section,
            project=project,
            scope=scope,
        )
        if not updated:
            return f"Failed to update memory {memory_id}."

        new_section = section if section is not None else mem.section
        new_project = project if project is not None else mem.project
        return f"Updated memory {memory_id}: {new_project}" + (
            f" / {new_section}" if new_section else ""
        )
    finally:
        store.close()


@mcp.tool()
def mem_forget(memory_id: int) -> str:
    """Delete a memory by ID.

    Use this to remove stale, wrong, or duplicate memories.
    Find the ID via mem_search first, then pass it here.

    Args:
        memory_id: The ID of the memory to delete (shown in search results)
    """
    store = get_store()
    try:
        mem = store.get(memory_id)
        if not mem:
            return f"Memory {memory_id} not found."

        store.delete(memory_id)
        return (
            f"Deleted memory {memory_id}: "
            f"{mem.project} / {mem.section or '(root)'} — {mem.snippet[:80]}"
        )
    finally:
        store.close()


@mcp.tool()
def mem_verify(memory_id: int) -> str:
    """Mark a memory as verified without changing its content.

    Use this when you've checked that a memory is still accurate but
    nothing needs to change. Updates the verified timestamp so future
    sessions can judge freshness without guessing from the creation date.

    Args:
        memory_id: The ID of the memory to verify (shown in search/recall results)
    """
    store = get_store()
    try:
        mem = store.get(memory_id)
        if not mem:
            return f"Memory {memory_id} not found."
        updated = store.verify(memory_id)
        if not updated:
            return f"Failed to verify memory {memory_id}."
        return (
            f"Verified memory {memory_id}: "
            f"{mem.project} / {mem.section or '(root)'} — {mem.snippet[:80]}"
        )
    finally:
        store.close()


@mcp.tool()
def mem_ingest() -> str:
    """Refresh the memory index by re-ingesting all memory files.

    Reads Claude Code and Gemini CLI memory files and updates the
    searchable index. Run this when you know memory files have changed.
    """
    store = get_store()
    try:
        claude_added = ingest_claude_memory(store)
        gemini_added = ingest_gemini_memory(store)
        copilot_added = ingest_copilot_memory(store)
        promoted = store.auto_promote_patterns()
        total = store.count()
        stats = store.stats()

        lines = [
            f"Ingested: {claude_added + gemini_added + copilot_added} new memories ({total} total)",
        ]
        if promoted:
            lines.append(f"Auto-promoted: {promoted} memories to global scope")
        lines.append(f"Projects ({len(stats)}):")
        for proj, count in stats.items():
            lines.append(f"  {proj}: {count}")

        return "\n".join(lines)
    finally:
        store.close()


@mcp.tool()
def mem_init(cwd: str | None = None, project: str | None = None) -> str:
    """Index project documentation files for cross-tool recall.

    Scans the project directory for README.md, CLAUDE.md, CONTRIBUTING.md,
    ARCHITECTURE.md, and .github/copilot-instructions.md, then stores
    them as searchable memories.

    Re-runnable: unchanged content is skipped, new content is added.

    Args:
        cwd: Project directory to scan (defaults to os.getcwd())
        project: Project name (auto-detected from git remote or directory)
    """
    from crossmem.ingest import derive_project_name

    store = get_store()
    try:
        project_dir = Path(cwd) if cwd else Path(os.getcwd())

        if not project:
            project = derive_project_name(project_dir)

        added = ingest_project_docs(store, project_dir, project=project)
        total = len(store.get_by_project(project))

        if added == 0 and total > 0:
            return f"'{project}' already up to date ({total} memories)."
        elif added == 0:
            return (
                f"No documentation files found in {project_dir}.\n"
                "Looked for: README.md, CLAUDE.md, CONTRIBUTING.md, "
                "ARCHITECTURE.md, .github/copilot-instructions.md"
            )
        return f"Initialized '{project}': {added} new memories ({total} total)"
    finally:
        store.close()


@mcp.tool()
def mem_deduplicate(
    project: str | None = None,
    dry_run: bool = True,
    threshold: float | None = None,
) -> str:
    """Scan the memory store for near-duplicate entries and optionally remove them.

    Near-duplicates are memory pairs whose embedding cosine distance is below the
    dedup threshold (default 0.05). The older record (lower id) is kept; the newer
    one is removed.

    Args:
        project: Limit scan to a specific project (default: scan all projects)
        dry_run: When True (default), report pairs without deleting anything
        threshold: Override cosine distance threshold (0.0–1.0; lower = stricter)

    Returns a report listing each duplicate pair with content snippets and the
    action taken (or "would remove" in dry_run mode).
    """
    store = get_store()
    try:
        pairs = store.scan_near_duplicates(project=project, threshold=threshold)

        if not pairs:
            scope_note = f" in project '{project}'" if project else ""
            return f"No near-duplicate memories found{scope_note}."

        lines: list[str] = []
        removed = 0

        for keeper, dup, distance in pairs:
            keeper_snippet = keeper.content[:120].replace("\n", " ").strip()
            dup_snippet = dup.content[:120].replace("\n", " ").strip()
            if len(keeper.content) > 120:
                keeper_snippet += "..."
            if len(dup.content) > 120:
                dup_snippet += "..."

            lines.append(
                f"KEEP  [{keeper.id}] ({keeper.project}/{keeper.section}): {keeper_snippet}"
            )
            lines.append(
                f"  DUP [{dup.id}] ({dup.project}/{dup.section}, dist={distance:.4f}): {dup_snippet}"
            )

            if not dry_run:
                store.delete(dup.id)
                removed += 1
                lines.append(f"  → Deleted [{dup.id}]")
            else:
                lines.append(f"  → Would delete [{dup.id}] (dry_run=True)")

            lines.append("")

        summary = f"Found {len(pairs)} near-duplicate pair(s)."
        if not dry_run:
            summary += f" Removed {removed}."
        else:
            summary += f" Re-run with dry_run=False to remove {len(pairs)} duplicate(s)."

        return summary + "\n\n" + "\n".join(lines).rstrip()
    finally:
        store.close()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
