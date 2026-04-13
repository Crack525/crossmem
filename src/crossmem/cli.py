"""CLI interface for crossmem."""

import datetime
import json
import os
import platform
import re
import shutil
from pathlib import Path

import click

from crossmem.ingest import (
    has_project_docs,
    ingest_claude_memory,
    ingest_copilot_memory,
    ingest_gemini_memory,
    ingest_project_docs,
)
from crossmem.store import DEFAULT_DB_PATH, MemoryStore


@click.group()
@click.version_option()
def main() -> None:
    """Cross-project memory for AI coding agents."""


@main.command()
def ingest() -> None:
    """Ingest memory files from AI coding tools."""
    store = MemoryStore()
    try:
        click.echo("Ingesting Claude Code memories...")
        added = ingest_claude_memory(store)
        click.echo("Ingesting Gemini CLI memories...")
        added += ingest_gemini_memory(store)
        click.echo("Ingesting GitHub Copilot memories...")
        added += ingest_copilot_memory(store)
        total = store.count()
        stats = store.stats()

        click.echo(f"\nAdded {added} new memories ({total} total)")
        click.echo(f"Database: {DEFAULT_DB_PATH}")
        click.echo(f"\nProjects ({len(stats)}):")
        for project, count in stats.items():
            click.echo(f"  {project}: {count} memories")
    finally:
        store.close()


@main.command()
@click.argument("query")
@click.option("-p", "--project", default=None, help="Filter by project name")
@click.option("-n", "--limit", default=10, help="Max results")
def search(query: str, project: str | None, limit: int) -> None:
    """Search across all project memories."""
    store = MemoryStore()
    try:
        results = store.search(query, limit=limit, project=project)

        if not results:
            click.echo(f'No results for "{query}"')
            return

        click.echo(f'Found {len(results)} results for "{query}":\n')
        for i, result in enumerate(results, 1):
            mem = result.memory
            click.echo(f"[{i}] {mem.project} / {mem.section or '(root)'} (id: {mem.id})")
            click.echo(f"    Source: {mem.source_file.split('/')[-1]}")
            click.echo(f"    {mem.snippet}")
            click.echo()
    finally:
        store.close()


@main.command()
@click.argument("memory_id", type=int, required=False)
@click.option("-p", "--project", default=None, help="Delete all memories for a project")
@click.option("--confirm", is_flag=True, help="Skip confirmation prompt")
def forget(memory_id: int | None, project: str | None, confirm: bool) -> None:
    """Delete memories by ID or by project.

    Examples:
        crossmem forget 42          # delete memory #42
        crossmem forget -p old-app  # delete all memories for old-app
    """
    if not memory_id and not project:
        click.echo("Provide a memory ID or --project. See: crossmem forget --help")
        return

    store = MemoryStore()
    try:
        if memory_id:
            mem = store.get(memory_id)
            if not mem:
                click.echo(f"Memory {memory_id} not found.")
                return
            click.echo(f"  [{mem.id}] {mem.project} / {mem.section or '(root)'}")
            click.echo(f"  {mem.snippet}")
            if not confirm and not click.confirm("Delete this memory?"):
                return
            store.delete(memory_id)
            click.echo(f"Deleted memory {memory_id}.")
        elif project:
            count = len(store.get_by_project(project))
            if count == 0:
                click.echo(f'No memories found for project "{project}".')
                return
            click.echo(f'Found {count} memories for "{project}".')
            if not confirm and not click.confirm(f"Delete all {count}?"):
                return
            deleted = store.delete_by_project(project)
            click.echo(f"Deleted {deleted} memories.")
    finally:
        store.close()


@main.command()
@click.argument("memory_id", type=int)
@click.argument("content")
@click.option("-s", "--section", default=None, help="New section (keeps current if omitted)")
@click.option("-p", "--project", default=None, help="New project (keeps current if omitted)")
def update(memory_id: int, content: str, section: str | None, project: str | None) -> None:
    """Update a memory in place, preserving its ID.

    Examples:
        crossmem update 42 "corrected content"
        crossmem update 42 "moved" -s Experiments
    """
    store = MemoryStore()
    try:
        mem = store.get(memory_id)
        if not mem:
            click.echo(f"Memory {memory_id} not found.")
            return
        updated = store.update(memory_id, content, section=section, project=project)
        if updated:
            new_project = project or mem.project
            new_section = section if section is not None else mem.section
            label = f"'{new_project}'"
            if new_section:
                label += f" / {new_section}"
            click.echo(f"Updated memory {memory_id}: {label}")
        else:
            click.echo(f"Failed to update memory {memory_id}.")
    finally:
        store.close()


@main.command()
@click.argument("content")
@click.option("-p", "--project", required=True, help="Project name")
@click.option("-s", "--section", default="", help="Section heading (e.g. Security, Patterns)")
def save(content: str, project: str, section: str) -> None:
    """Save a memory from the command line.

    Examples:
        crossmem save "Use retry with backoff" -p backend-api -s Patterns
    """
    store = MemoryStore()
    try:
        result = store.add(content, "cli:save", project, section)
        if result is None:
            click.echo(f"Memory already exists for project '{project}'.")
        else:
            label = f"'{project}'"
            if section:
                label += f" / {section}"
            click.echo(f"Saved to {label} (id: {result})")
    finally:
        store.close()


@main.command()
@click.option("--port", default=8765, help="Port for local server")
def graph(port: int) -> None:
    """Visualize the knowledge graph in your browser."""
    from crossmem.graph import serve_graph

    store = MemoryStore()
    if store.count() == 0:
        click.echo("No memories yet. Run: crossmem ingest")
        store.close()
        return
    serve_graph(store, port=port)  # closes store internally before serving


@main.command()
@click.option("-p", "--project", default=None, help="Sync this project + shared patterns")
def sync(project: str | None) -> None:
    """Sync Claude Code memories → Gemini CLI (one-shot)."""
    click.echo(
        "Note: sync is deprecated. Use `crossmem install-instructions`"
        " + MCP mem_recall() instead."
    )
    from crossmem.sync import sync_once

    count, changed = sync_once(project=project)
    if changed:
        label = f"{project} + shared patterns" if project else "all"
        click.echo(f"Synced {count} memories ({label}) → ~/.gemini/GEMINI.md")
    else:
        click.echo(f"Already in sync ({count} memories)")


@main.command(name="sync-watch")
@click.option("--interval", default=30, help="Poll interval in seconds")
@click.option("-p", "--project", default=None, help="Sync this project + shared patterns")
def sync_watch(interval: int, project: str | None) -> None:
    """Watch Claude memories and sync to Gemini on changes."""
    click.echo(
        "Note: sync-watch is deprecated. Use `crossmem install-instructions`"
        " + MCP mem_recall() instead."
    )
    from crossmem.sync import watch

    watch(interval=interval, project=project)


@main.command()
@click.pass_context
def setup(ctx: click.Context) -> None:
    """One-time setup: hook + instructions + ingest.

    Runs install-hook (Claude Code), install-hook --tool copilot (workspace),
    install-instructions (Gemini), and ingest (pull existing memories).
    """
    click.echo("Setting up crossmem...\n")

    click.echo("1. Claude Code hook")
    ctx.invoke(install_hook, tool="claude", uninstall=False, dry_run=False,
               global_=False, project=None)
    click.echo()

    click.echo("2. Copilot instructions (workspace)")
    ctx.invoke(install_hook, tool="copilot", uninstall=False, dry_run=False,
               global_=False, project=None)
    click.echo()

    click.echo("3. Gemini instructions")
    ctx.invoke(install_instructions, uninstall=False, dry_run=False)
    click.echo()

    click.echo("4. Ingesting existing memories")
    ctx.invoke(ingest)
    click.echo()

    click.echo("Done. Memories will load automatically in all tools.")


@main.command()
def serve() -> None:
    """Start the MCP server (stdio transport)."""
    from crossmem.server import main as serve_main

    serve_main()


@main.command()
def stats() -> None:
    """Show memory statistics."""
    store = MemoryStore()
    try:
        total = store.count()
        projects = store.stats()

        if total == 0:
            click.echo("No memories yet. Run: crossmem ingest")
            return

        click.echo(f"Total memories: {total}")
        click.echo(f"Projects: {len(projects)}\n")
        for project, count in projects.items():
            click.echo(f"  {project}: {count}")
        click.echo(f"\nDatabase: {DEFAULT_DB_PATH}")
    finally:
        store.close()


@main.command()
@click.option("-p", "--project", default=None, help="Project name (auto-detected from cwd/git)")
@click.option(
    "--path", "project_path", default=None, type=click.Path(exists=True),
    help="Project directory (defaults to cwd)",
)
def init(project: str | None, project_path: str | None) -> None:
    """Index project documentation for cross-tool recall.

    Scans the project directory for knowledge files (README.md, CLAUDE.md,
    CONTRIBUTING.md, ARCHITECTURE.md, .github/copilot-instructions.md)
    and stores them as searchable memories.

    Re-runnable: unchanged content is skipped, new content is added.

    Examples:
        crossmem init                     # current directory
        crossmem init -p my-api           # explicit project name
        crossmem init --path ~/projects/backend
    """
    from crossmem.ingest import derive_project_name

    project_dir = Path(project_path) if project_path else Path.cwd()
    if project is None:
        project = derive_project_name(project_dir)

    store = MemoryStore()
    try:
        added = ingest_project_docs(store, project_dir, project=project)
        total = len(store.get_by_project(project))

        if added == 0 and total > 0:
            click.echo(f"'{project}' already up to date ({total} memories).")
        elif added == 0:
            click.echo(
                f"No documentation files found in {project_dir}.\n"
                "Looked for: README.md, CLAUDE.md, CONTRIBUTING.md, "
                "ARCHITECTURE.md, .github/copilot-instructions.md"
            )
        else:
            click.echo(
                f"Initialized '{project}': {added} new memories "
                f"({total} total)"
            )
    finally:
        store.close()


# init: doc priority for tiered recall (lower = higher priority)
_INIT_DOC_PRIORITY = {
    "CLAUDE.md": 0,
    "copilot-instructions.md": 0,
    "CONTRIBUTING.md": 1,
    "ARCHITECTURE.md": 2,
    "README.md": 3,
}


def _source_tier(source_file: str) -> int:
    """Assign a tier to a memory based on its source.

    Tier 0: mem_save (human/AI-curated)
    Tier 1: ingested tool memories (Claude/Copilot/Gemini files)
    Tier 2: init docs — rules & conventions (CLAUDE.md, copilot-instructions)
    Tier 3: init docs — dev workflow (CONTRIBUTING.md)
    Tier 4: init docs — architecture (ARCHITECTURE.md)
    Tier 5: init docs — general (README.md)
    """
    if source_file.startswith("mcp:mem_save") or source_file == "cli:save":
        return 0
    if not source_file.startswith("init:"):
        return 1
    # Extract filename from init:path
    raw = source_file.removeprefix("init:")
    filename = raw.split("/")[-1] if "/" in raw else raw
    priority = _INIT_DOC_PRIORITY.get(filename, 3)
    return 2 + priority


def _build_recall_output(
    project: str,
    project_memories: list,
    shared_memories: list,
    budget: int,
) -> str:
    """Build recall output within a character budget, filling by tier."""
    # Sort project memories by tier, then recency (already sorted by recency)
    tiered = sorted(project_memories, key=lambda m: _source_tier(m.source_file))

    lines = [f"# crossmem: {project}\n"]
    used = len(lines[0]) + 1

    for mem in tiered:
        section = f" [{mem.section}]" if mem.section else ""
        line = f"- {mem.snippet}{section}"
        if used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1

    if shared_memories and used < budget:
        header = "\n## Cross-project patterns\n"
        used += len(header)
        lines.append(header)
        for mem in shared_memories:
            label = (
                f"{mem.project}/{mem.section}" if mem.section else mem.project
            )
            line = f"- ({label}) {mem.snippet}"
            if used + len(line) + 1 > budget:
                break
            lines.append(line)
            used += len(line) + 1

    return "\n".join(lines)


@main.command()
@click.option("-p", "--project", default=None, help="Project name (auto-detected from cwd)")
@click.option("-n", "--limit", default=30, help="Max memories to fetch from DB")
@click.option("--budget", default=2000, help="Max output size in characters")
@click.option(
    "--format", "fmt",
    type=click.Choice(["text", "copilot", "vscode"], case_sensitive=False),
    default="text",
    help="Output format: text (default), copilot (injection markers), or vscode (hook JSON)",
)
def recall(project: str | None, limit: int, budget: int, fmt: str) -> None:
    """Recall memories for the current project (for use as a hook).

    Outputs project memories and cross-project patterns as text,
    prioritized by tier within a character budget:

      1. Curated memories (mem_save)
      2. Ingested tool memories (Claude/Copilot/Gemini)
      3. Project docs (CLAUDE.md > CONTRIBUTING.md > README.md)
      4. Cross-project patterns

    Use --format copilot to wrap output in auto-injection markers
    (for piping into .github/copilot-instructions.md).

    Designed to be used as a Claude Code SessionStart hook:

        crossmem install-hook

    Can also be used standalone:

        crossmem recall
        crossmem recall -p backend-api
        crossmem recall --budget 4000
        crossmem recall --format copilot
    """
    output = _get_recall_content(project, limit, budget)
    if output is None:
        return
    if fmt == "copilot":
        click.echo(_build_copilot_block(output))
    elif fmt == "vscode":
        click.echo(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": output,
            }
        }))
    else:
        click.echo(output)


@main.command(name="prompt-search")
def prompt_search() -> None:
    """Search memories based on the user's prompt (for UserPromptSubmit hook).

    Reads the hook JSON from stdin, extracts the prompt text,
    searches crossmem for relevant memories, and outputs them to stdout.
    Claude Code injects stdout as additionalContext before generating.

    This command is not meant to be run manually — it's installed
    as a UserPromptSubmit hook by `crossmem install-hook`.
    """
    import sys

    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return

    prompt = hook_input.get("prompt", "")
    if not prompt or len(prompt.split()) < PROMPT_SEARCH_MIN_WORDS:
        return

    # Extract meaningful search terms — skip very common words
    import re as _re

    stop_words = {
        "i", "me", "my", "we", "our", "you", "your", "it", "its", "he", "she", "they",
        "a", "an", "the", "this", "that", "these", "those",
        "is", "am", "are", "was", "were", "be", "been", "being",
        "do", "does", "did", "will", "would", "could", "should", "can", "may", "might",
        "have", "has", "had", "having",
        "in", "on", "at", "to", "for", "of", "with", "by", "from", "about",
        "and", "or", "but", "not", "no", "if", "when", "how", "what", "which", "where",
        "so", "just", "also", "very", "too", "here", "there",
        "now", "then", "up", "down", "still", "let", "want", "need", "use", "get", "make",
        "yes", "ok", "okay", "sure", "please", "thanks", "thank",
        "go", "ahead", "good", "great", "looks", "look", "like", "continue", "done", "right",
        "actually", "pretty", "really", "stuff", "things", "thing", "something", "anything",
        "interesting", "cool", "nice", "fine", "bad", "better", "best", "worst", "new", "old",
        "know", "think", "see", "try", "show", "tell", "give", "take", "put", "run", "set",
        "way", "work", "works", "well", "much", "many", "some", "any", "all", "each", "every",
    }
    # Strip all non-alphanumeric chars (FTS5 special chars: ? * : ( ) + ^ ~ { })
    keywords = [_re.sub(r"[^a-z0-9]", "", w) for w in prompt.lower().split()]
    keywords = [w for w in keywords if w and w not in stop_words]
    if not keywords:
        return

    search_query = " ".join(keywords)
    store = MemoryStore()
    try:
        results = store.search(search_query, limit=PROMPT_SEARCH_MAX_RESULTS, or_mode=True)
        # Filter weak matches — BM25 rank is more negative for stronger matches.
        # Skip filtering when: rank ~0 (tiny DB), or single keyword (specific/intentional).
        if len(keywords) > 1 and results and results[0].rank < -1.0:
            results = [r for r in results if r.rank <= PROMPT_SEARCH_MIN_RANK]
        if not results:
            return

        lines = ["# crossmem: relevant memories"]
        used = len(lines[0]) + 1
        for r in results:
            mem = r.memory
            section = f" [{mem.section}]" if mem.section else ""
            project = f"({mem.project})"
            line = f"- {project}{section} {mem.snippet}"
            if used + len(line) + 1 > PROMPT_SEARCH_BUDGET:
                break
            lines.append(line)
            used += len(line) + 1

        if len(lines) > 1:
            output = "\n".join(lines)
            # VS Code hooks pass hookEventName — wrap output in JSON for additionalContext
            if hook_input.get("hookEventName"):
                click.echo(json.dumps({
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": output,
                    }
                }))
            else:
                click.echo(output)
    finally:
        store.close()


def _find_crossmem_bin() -> str:
    """Find the crossmem binary path, preferring an absolute path.

    The hook runs in Claude Code's shell which may not have the
    venv activated, so we need an absolute path to survive.
    """
    # If running from a venv, use the venv's binary directly
    import sys
    venv_bin = Path(sys.executable).parent / "crossmem"
    if venv_bin.exists():
        return str(venv_bin)
    crossmem_bin = shutil.which("crossmem")
    if crossmem_bin:
        return crossmem_bin
    return "crossmem"


def _claude_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _read_settings(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise click.ClickException(
                f"Malformed JSON in {path}: {e}. Fix the file manually before running install-hook."
            )
    return {}


def _write_settings(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


HOOK_MATCHER = "startup|compact|resume"
HOOK_MATCHER_LEGACY = "crossmem-recall"
PROMPT_SEARCH_MIN_WORDS = 3
PROMPT_SEARCH_MAX_RESULTS = 5
PROMPT_SEARCH_BUDGET = 4000
PROMPT_SEARCH_MIN_RANK = -5.0  # BM25 rank: more negative = stronger match; filter weak hits


INSTRUCTION_LINE = (
    "At the start of every session and after every conversation compaction, "
    "call mem_recall() to load cross-project context from crossmem."
)
INSTRUCTION_MARKER = "<!-- crossmem-instruction -->"

COPILOT_CONTENT_MARKER_START = "<!-- crossmem:auto-injected"
COPILOT_CONTENT_MARKER_END = "<!-- crossmem:end -->"


def _copilot_global_path() -> Path:
    """Return platform-appropriate VS Code global Copilot instructions path."""
    system = platform.system()
    if system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Code" / "User" / "prompts" / "copilot-instructions.md"
    elif system == "Darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Code"
            / "User"
            / "prompts"
            / "copilot-instructions.md"
        )
    else:  # Linux
        return Path.home() / ".config" / "Code" / "User" / "prompts" / "copilot-instructions.md"


def _build_copilot_block(output: str) -> str:
    """Wrap recalled output in Copilot auto-injection markers."""
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    return (
        f"{COPILOT_CONTENT_MARKER_START} {ts} "
        f"— regenerate: crossmem install-hook --tool copilot -->\n"
        f"{output}\n"
        f"{COPILOT_CONTENT_MARKER_END}\n"
    )


def _parse_block_timestamp(content: str) -> datetime.datetime | None:
    """Extract the ISO timestamp from a crossmem block header, or None if absent/unparseable."""
    m = re.search(
        r"<!-- crossmem:auto-injected (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})",
        content,
    )
    if not m:
        return None
    try:
        return datetime.datetime.fromisoformat(m.group(1))
    except ValueError:
        return None


def _strip_copilot_block(content: str) -> str:
    """Remove the crossmem auto-injected block from content, returning cleaned text."""
    lines = content.split("\n")
    result = []
    inside_block = False
    for line in lines:
        if COPILOT_CONTENT_MARKER_START in line:
            inside_block = True
            continue
        if inside_block and COPILOT_CONTENT_MARKER_END in line:
            inside_block = False
            continue
        if not inside_block:
            result.append(line)
    return "\n".join(result).rstrip()


def _inject_copilot_block(path: Path, block: str, dry_run: bool) -> bool:
    """Write block into path using marker-based replacement.

    Replaces the existing auto-injected block if present, otherwise appends.
    Returns True if the file was changed (or would be changed in dry-run).
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="replace")

    if COPILOT_CONTENT_MARKER_START in existing:
        cleaned = _strip_copilot_block(existing)
        new_content = (cleaned + "\n\n" if cleaned else "") + block
        if new_content == existing:
            return False
        if not dry_run:
            path.write_text(new_content, encoding="utf-8")
        return True
    else:
        new_content = (existing.rstrip() + "\n\n" if existing.strip() else "") + block
        if not dry_run:
            path.write_text(new_content, encoding="utf-8")
        return True


def _get_recall_content(project: str | None, limit: int, budget: int) -> str | None:
    """Return recall output string, or None if no memories found.

    Shared by `recall` CLI command and `install-hook --tool copilot`.
    """
    from crossmem.ingest import (
        derive_project_name,
        ingest_claude_memory,
        ingest_copilot_memory,
        ingest_gemini_memory,
    )
    from crossmem.server import resolve_project

    store = MemoryStore()
    try:
        ingest_claude_memory(store)
        ingest_copilot_memory(store)
        ingest_gemini_memory(store)

        cwd = os.getcwd()
        project_dir = Path(cwd)

        if not project:
            known = store.list_projects()
            project = resolve_project(cwd, known)
            if not project:
                if has_project_docs(project_dir):
                    project = derive_project_name(project_dir)
                    ingest_project_docs(store, project_dir, project=project)
                else:
                    return None

        project_memories = store.get_by_project(project, limit=limit)
        shared_memories = store.get_shared_sections(project, limit=min(limit, 10))

        if not project_memories and not shared_memories:
            if has_project_docs(project_dir):
                ingest_project_docs(store, project_dir, project=project)
                project_memories = store.get_by_project(project, limit=limit)
                shared_memories = store.get_shared_sections(project, limit=min(limit, 10))
            if not project_memories and not shared_memories:
                return None

        return _build_recall_output(project, project_memories, shared_memories, budget)
    finally:
        store.close()


def _append_instruction(path: Path, dry_run: bool) -> bool:
    """Append or update crossmem instruction in a config file.

    Returns True if the file was changed (or would be changed in dry-run).
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    block = f"{INSTRUCTION_MARKER}\n{INSTRUCTION_LINE}\n"

    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="replace")
        if INSTRUCTION_MARKER in existing:
            if INSTRUCTION_LINE in existing:
                return False  # already up-to-date
            # Marker present but content is stale — replace it
            if not dry_run:
                updated = _replace_instruction_block(existing, block)
                path.write_text(updated, encoding="utf-8")
            return True

    if dry_run:
        return True

    prefix = existing.rstrip() + "\n\n" if existing.strip() else ""
    path.write_text(prefix + block, encoding="utf-8")
    return True


def _replace_instruction_block(content: str, new_block: str) -> str:
    """Replace the existing crossmem instruction block with a new one."""
    lines = content.split("\n")
    result = []
    skip_next = False
    for line in lines:
        if INSTRUCTION_MARKER in line:
            skip_next = True
            continue
        if skip_next:
            skip_next = False
            continue
        result.append(line)
    cleaned = "\n".join(result).rstrip()
    prefix = cleaned + "\n\n" if cleaned else ""
    return prefix + new_block


def _remove_instruction(path: Path) -> bool:
    """Remove crossmem instruction from a config file. Returns True if changed."""
    if not path.exists():
        return False

    content = path.read_text(encoding="utf-8", errors="replace")
    if INSTRUCTION_MARKER not in content:
        return False

    # Remove the marker line and instruction line
    lines = content.split("\n")
    filtered = []
    skip_next = False
    for line in lines:
        if INSTRUCTION_MARKER in line:
            skip_next = True
            continue
        if skip_next:
            skip_next = False
            continue
        filtered.append(line)

    # Clean up trailing blank lines
    result = "\n".join(filtered).rstrip() + "\n" if any(filtered) else ""
    path.write_text(result, encoding="utf-8")
    return True


@main.command(name="install-instructions")
@click.option("--uninstall", is_flag=True, help="Remove instructions")
@click.option("--dry-run", is_flag=True, help="Show what would change")
def install_instructions(uninstall: bool, dry_run: bool) -> None:
    """Add 'call mem_recall' instruction to Gemini config.

    For Gemini CLI: appends a one-line instruction so the LLM calls
    mem_recall at session start.

    For GitHub Copilot: use the newer command instead, which injects
    actual recalled memories (not just a directive):

        crossmem install-hook --tool copilot

    Target files:
        ~/.gemini/GEMINI.md  (global)
    """
    # Copilot is handled by install-hook --tool copilot (injects content, not directive)
    targets = {
        "Gemini": Path.home() / ".gemini" / "GEMINI.md",
    }

    if uninstall:
        for name, path in targets.items():
            if _remove_instruction(path):
                click.echo(f"Removed crossmem instruction from {name}: {path}")
            else:
                click.echo(f"{name}: no crossmem instruction found")
        return

    for name, path in targets.items():
        changed = _append_instruction(path, dry_run)
        if dry_run:
            action = "already present" if not changed else "would add"
            click.echo(f"{name}: {action} in {path}")
        elif changed:
            click.echo(f"Added crossmem instruction to {name}: {path}")
        else:
            click.echo(f"{name}: instruction already present")


@main.command(name="install-hook")
@click.option("--uninstall", is_flag=True, help="Remove the hook instead of installing")
@click.option("--dry-run", is_flag=True, help="Show what would change without writing")
@click.option(
    "--tool",
    type=click.Choice(["claude", "copilot", "copilot-agent"], case_sensitive=False),
    default="claude",
    help="Target tool: claude (default), copilot (instructions.md), or copilot-agent (VS Code hooks)",
)
@click.option(
    "--global", "global_",
    is_flag=True,
    help="[copilot only] Write to VS Code global user prompts (applies to all workspaces)",
)
@click.option("-p", "--project", default=None, help="[copilot only] Project name (auto-detected)")
@click.option("-n", "--limit", default=30, help="[copilot only] Max memories to inject")
@click.option("--budget", default=2000, help="[copilot only] Max injected content size in characters")
@click.option(
    "--if-stale",
    is_flag=True,
    help="[copilot only] Only re-inject if existing block is older than --max-age minutes",
)
@click.option(
    "--max-age",
    default=30,
    help="[copilot only] Block age in minutes before --if-stale triggers a refresh (default: 30)",
)
def install_hook(
    uninstall: bool,
    dry_run: bool,
    tool: str,
    global_: bool,
    project: str | None,
    limit: int,
    budget: int,
    if_stale: bool,
    max_age: int,
) -> None:
    """Add a SessionStart hook to Claude Code settings, or inject context for Copilot.

    Claude Code (default):
        Installs a hook that automatically loads crossmem memories
        at the start of every Claude Code session.

            crossmem install-hook
            crossmem install-hook --uninstall

    GitHub Copilot (agent mode):
        Creates .github/hooks/crossmem.json with SessionStart and
        UserPromptSubmit hooks for VS Code agent mode (Preview).

            crossmem install-hook --tool copilot-agent
            crossmem install-hook --tool copilot-agent --uninstall

    GitHub Copilot (instructions):
        Injects recalled memories directly into copilot-instructions.md.
        Uses marker-based replacement so re-running is idempotent.

            crossmem install-hook --tool copilot               # workspace
            crossmem install-hook --tool copilot --global      # all workspaces
            crossmem install-hook --tool copilot --uninstall   # remove block

        Stale-check (for cron / shell precmd / launchd):
            crossmem install-hook --tool copilot --if-stale            # refresh if >30 min old
            crossmem install-hook --tool copilot --if-stale --max-age 60  # custom threshold
            # Exits silently (no output) when block is fresh.
    """
    if tool == "claude" and (if_stale or max_age != 30):
        click.echo("Warning: --if-stale and --max-age are only used with --tool copilot.")
    if tool == "copilot-agent":
        _install_hook_copilot_agent(uninstall=uninstall, dry_run=dry_run)
    elif tool == "copilot":
        _install_hook_copilot(
            uninstall=uninstall,
            dry_run=dry_run,
            global_=global_,
            project=project,
            limit=limit,
            budget=budget,
            if_stale=if_stale,
            max_age=max_age,
        )
    else:
        _install_hook_claude(uninstall=uninstall, dry_run=dry_run)


def _install_hook_claude(uninstall: bool, dry_run: bool) -> None:
    """Install or remove the Claude Code SessionStart and UserPromptSubmit hooks."""
    settings_path = _claude_settings_path()
    settings = _read_settings(settings_path)

    hooks = settings.get("hooks", {})
    crossmem_bin = _find_crossmem_bin()

    # --- SessionStart hook ---
    session_start = hooks.get("SessionStart", [])
    ss_idx = None
    for i, entry in enumerate(session_start):
        matcher = entry.get("matcher", "")
        hooks_list = entry.get("hooks", [])
        has_crossmem_cmd = any("crossmem recall" in h.get("command", "") for h in hooks_list)
        if matcher in (HOOK_MATCHER, HOOK_MATCHER_LEGACY) or has_crossmem_cmd:
            ss_idx = i
            break

    # --- UserPromptSubmit hook ---
    prompt_submit = hooks.get("UserPromptSubmit", [])
    ups_idx = None
    for i, entry in enumerate(prompt_submit):
        hooks_list = entry.get("hooks", [])
        has_crossmem_cmd = any(
            "crossmem prompt-search" in h.get("command", "") for h in hooks_list
        )
        if has_crossmem_cmd:
            ups_idx = i
            break

    if uninstall:
        removed = False
        if ss_idx is not None:
            if not dry_run:
                session_start.pop(ss_idx)
                if not session_start:
                    del hooks["SessionStart"]
            removed = True
        if ups_idx is not None:
            if not dry_run:
                prompt_submit.pop(ups_idx)
                if not prompt_submit:
                    del hooks["UserPromptSubmit"]
            removed = True
        if removed:
            if dry_run:
                click.echo(f"Would remove crossmem hooks from {settings_path}")
                return
            if not hooks:
                settings.pop("hooks", None)
            _write_settings(settings_path, settings)
            click.echo("Removed crossmem hooks from Claude Code settings.")
        else:
            click.echo("No crossmem hooks found in Claude Code settings.")
        return

    ss_entry = {
        "matcher": HOOK_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": f"{crossmem_bin} recall",
            }
        ],
    }

    ups_entry = {
        "hooks": [
            {
                "type": "command",
                "command": f"{crossmem_bin} prompt-search",
            }
        ],
    }

    if dry_run:
        click.echo(f"Would install in {settings_path}:\n")
        click.echo(json.dumps({
            "hooks": {
                "SessionStart": [ss_entry],
                "UserPromptSubmit": [ups_entry],
            }
        }, indent=2))
        return

    # Install/update SessionStart
    if ss_idx is not None:
        session_start[ss_idx] = ss_entry
    else:
        session_start.append(ss_entry)
    hooks["SessionStart"] = session_start

    # Install/update UserPromptSubmit
    if ups_idx is not None:
        prompt_submit[ups_idx] = ups_entry
    else:
        prompt_submit.append(ups_entry)
    hooks["UserPromptSubmit"] = prompt_submit

    settings["hooks"] = hooks
    _write_settings(settings_path, settings)

    action = "Updated" if (ss_idx is not None or ups_idx is not None) else "Installed"
    click.echo(f"{action} crossmem hooks in Claude Code settings.")
    click.echo(f"  SessionStart: {crossmem_bin} recall")
    click.echo(f"  UserPromptSubmit: {crossmem_bin} prompt-search")
    click.echo(f"  Settings: {settings_path}")
    click.echo("\nMemories will load at session start AND before every response.")


def _install_hook_copilot_agent(uninstall: bool, dry_run: bool) -> None:
    """Install or remove VS Code agent-mode hooks in .github/hooks/crossmem.json."""
    hooks_dir = Path.cwd() / ".github" / "hooks"
    hooks_path = hooks_dir / "crossmem.json"
    crossmem_bin = _find_crossmem_bin()

    if uninstall:
        if not hooks_path.exists():
            click.echo("No crossmem hook found in .github/hooks/.")
            return
        if dry_run:
            click.echo(f"Would remove {hooks_path}")
            return
        hooks_path.unlink()
        # Remove empty dirs
        if hooks_dir.exists() and not any(hooks_dir.iterdir()):
            hooks_dir.rmdir()
        click.echo(f"Removed {hooks_path}")
        return

    config = {
        "hooks": {
            "SessionStart": [
                {
                    "type": "command",
                    "command": f"{crossmem_bin} recall --format vscode",
                }
            ],
            "UserPromptSubmit": [
                {
                    "type": "command",
                    "command": f"{crossmem_bin} prompt-search",
                }
            ],
        }
    }

    if dry_run:
        click.echo(f"Would create {hooks_path}:\n")
        click.echo(json.dumps(config, indent=2))
        return

    hooks_dir.mkdir(parents=True, exist_ok=True)
    hooks_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    click.echo(f"Installed VS Code agent hooks: {hooks_path}")
    click.echo(f"  SessionStart: {crossmem_bin} recall --format vscode")
    click.echo(f"  UserPromptSubmit: {crossmem_bin} prompt-search")
    click.echo("\nNote: UserPromptSubmit additionalContext is pending VS Code support.")


def _install_hook_copilot(
    uninstall: bool,
    dry_run: bool,
    global_: bool,
    project: str | None,
    limit: int,
    budget: int,
    if_stale: bool = False,
    max_age: int = 30,
) -> None:
    """Inject recalled memories into Copilot instructions file.

    Uses marker-based block replacement — idempotent, safe to re-run.
    For --uninstall, removes the injected block leaving other content intact.
    """
    if global_:
        target = _copilot_global_path()
        label = "global Copilot instructions"
    else:
        target = Path.cwd() / ".github" / "copilot-instructions.md"
        label = "workspace Copilot instructions"

    if uninstall:
        if not target.exists() or COPILOT_CONTENT_MARKER_START not in target.read_text(
            encoding="utf-8", errors="replace"
        ):
            click.echo(f"No crossmem block found in {target}")
            return
        if dry_run:
            click.echo(f"Would remove crossmem block from {target}")
            return
        content = target.read_text(encoding="utf-8", errors="replace")
        cleaned = _strip_copilot_block(content)
        target.write_text((cleaned + "\n") if cleaned else "", encoding="utf-8")
        click.echo(f"Removed crossmem block from {target}")
        return

    if if_stale and not uninstall:
        existing_text = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
        if COPILOT_CONTENT_MARKER_START in existing_text:
            ts = _parse_block_timestamp(existing_text)
            if ts is not None:
                age_minutes = (datetime.datetime.now() - ts).total_seconds() / 60
                if age_minutes < max_age:
                    # Fresh — skip silently
                    return

    output = _get_recall_content(project, limit, budget)
    if output is None:
        click.echo("No memories found. Run: crossmem ingest")
        return

    block = _build_copilot_block(output)

    if dry_run:
        click.echo(f"Would write to {target}:\n")
        click.echo(block)
        return

    changed = _inject_copilot_block(target, block, dry_run=False)
    if changed:
        click.echo(f"Injected crossmem context into {label}: {target}")
        if not global_:
            click.echo(
                "\n  Tip: add to .gitignore or re-run periodically to keep fresh.\n"
                "  Re-run: crossmem install-hook --tool copilot"
            )
    else:
        click.echo(f"{label}: already up to date")
