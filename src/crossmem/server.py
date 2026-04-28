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

_SESSION_FOOTER = (
    "_During this session: call mem_save() for any decision, gotcha, or pattern worth keeping. "
    "Call mem_update(id=...) to correct an existing memory rather than saving a duplicate._"
)
_MCP_RECALL_LIMIT: int = 10
# Hard limit for mem_save content. Prevents accidental full-file dumps that
# inflate the DB, degrade FTS5 ranking, and bloat future recall context.
_MEM_SAVE_MAX_CHARS: int = 1000


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
        results = store.search(query, limit=limit, project=project)

        if not results:
            return f'No results for "{query}"'

        lines = [f'Found {len(results)} results for "{query}":\n']
        for i, result in enumerate(results, 1):
            mem = result.memory
            lines.append(
                f"[{i}] {mem.project} / {mem.section or '(root)'} (id: {mem.id})"
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
                            f'Could not detect project from "{cwd}".',
                            f"Known projects: {', '.join(known)}",
                            "Pass an explicit project name to mem_recall(project=...).\n",
                            f"## Cross-project patterns ({len(global_mems)} available):\n",
                        ]
                        for mem in global_mems:
                            label = f"{mem.project} / {mem.section}" if mem.section else mem.project
                            lines.append(f"- (id: {mem.id}) **{label}**: {mem.snippet}")
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
                lines = [f'_(No scoped results for "{query}". Showing all {project} memories.)_\n']
                if project_memories:
                    lines.append(f"## {project} memories ({len(project_memories)}):\n")
                    for mem in project_memories:
                        section = f" / {mem.section}" if mem.section else ""
                        lines.append(f"- (id: {mem.id}) **{mem.project}{section}**: {mem.snippet}")
                    lines.append("")
                if shared_memories:
                    lines.append(
                        f"## Cross-project patterns ({len(shared_memories)} from other projects):\n"
                    )
                    for mem in shared_memories:
                        label = f"{mem.project} / {mem.section}" if mem.section else mem.project
                        lines.append(f"- (id: {mem.id}) **{label}**: {mem.snippet}")
                    lines.append("")
                lines.append(_SESSION_FOOTER)
                return "\n".join(lines)

            lines = [f"## {project} memories (scoped: {query!r}):\n"]
            for i, result in enumerate(results, 1):
                mem = result.memory
                section = f" / {mem.section}" if mem.section else ""
                lines.append(f"- (id: {mem.id}) **{mem.project}{section}**: {mem.snippet}")
            lines.append("")

            seen_ids = {r.memory.id for r in results}
            global_mems = store.get_global_memories(query=query, limit=5)
            global_mems = [m for m in global_mems if m.id not in seen_ids]
            if global_mems:
                lines.append(f"## Cross-project patterns (scoped: {query!r}):\n")
                for mem in global_mems:
                    label = f"{mem.project} / {mem.section}" if mem.section else mem.project
                    lines.append(f"- (id: {mem.id}) **{label}**: {mem.snippet}")
                lines.append("")

            lines.append(_SESSION_FOOTER)
            return "\n".join(lines)

        # Get project-specific memories from the store
        project_memories = store.get_by_project(project)

        # Auto-init if project exists but has no memories yet
        if not project_memories and has_project_docs(project_dir):
            ingest_project_docs(store, project_dir, project=project)
            project_memories = store.get_by_project(project)

        # Get globally scoped memories (cross-project patterns)
        shared_memories = store.get_global_memories(limit=20)

        lines = []

        if project_memories:
            lines.append(f"## {project} memories ({len(project_memories)}):\n")
            for mem in project_memories:
                section = f" / {mem.section}" if mem.section else ""
                lines.append(f"- (id: {mem.id}) **{mem.project}{section}**: {mem.snippet}")
            lines.append("")

        if shared_memories:
            lines.append(
                f"## Cross-project patterns ({len(shared_memories)} from other projects):\n"
            )
            for mem in shared_memories:
                label = f"{mem.project} / {mem.section}" if mem.section else mem.project
                lines.append(f"- (id: {mem.id}) **{label}**: {mem.snippet}")
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
    scope: str = "project",
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
        scope: 'project' (default) to keep this memory local to the project,
               or 'global' to surface it across all projects as a cross-cutting pattern
    """
    if not content or not content.strip():
        return "Content cannot be empty."
    if len(content.strip()) < 10:
        n = len(content.strip())
        return f"Content too short ({n} chars, min 10). Be specific and actionable."
    if len(content) > _MEM_SAVE_MAX_CHARS:
        return (
            f"Content too long ({len(content)} chars, max {_MEM_SAVE_MAX_CHARS}). "
            "Distill to one actionable sentence or short paragraph. "
            "Good: 'Deploy with uv publish --token $TOKEN; token in 1Password.' "
            "Bad: pasting README sections, code blocks, or full file contents."
        )
    if scope not in ("project", "global"):
        return f"Invalid scope '{scope}'. Use 'project' (default) or 'global'."

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
    if len(content) > _MEM_SAVE_MAX_CHARS:
        return (
            f"Content too long ({len(content)} chars, max {_MEM_SAVE_MAX_CHARS}). "
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


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
