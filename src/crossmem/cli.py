"""CLI interface for crossmem."""

import json
import os
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

    Runs install-hook (Claude Code), install-instructions (Copilot + Gemini),
    and ingest (pull existing memories) in one command.
    """
    click.echo("Setting up crossmem...\n")

    click.echo("1. Claude Code hook")
    ctx.invoke(install_hook)
    click.echo()

    click.echo("2. Copilot + Gemini instructions")
    ctx.invoke(install_instructions)
    copilot_path = Path.cwd() / ".github" / "copilot-instructions.md"
    if not copilot_path.parent.exists():
        click.echo(
            "  ⚠ No .github/ directory here — Copilot instructions were "
            "created but you may want to re-run from a project root."
        )
    click.echo()

    click.echo("3. Ingesting existing memories")
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
def recall(project: str | None, limit: int, budget: int) -> None:
    """Recall memories for the current project (for use as a hook).

    Outputs project memories and cross-project patterns as text,
    prioritized by tier within a character budget:

      1. Curated memories (mem_save)
      2. Ingested tool memories (Claude/Copilot/Gemini)
      3. Project docs (CLAUDE.md > CONTRIBUTING.md > README.md)
      4. Cross-project patterns

    Designed to be used as a Claude Code SessionStart hook:

        crossmem install-hook

    Can also be used standalone:

        crossmem recall
        crossmem recall -p backend-api
        crossmem recall --budget 4000
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
        # Auto-ingest native memories on every recall (startup, resume, compact)
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
                    return

        project_memories = store.get_by_project(project, limit=limit)
        shared_memories = store.get_shared_sections(
            project, limit=min(limit, 10)
        )

        if not project_memories and not shared_memories:
            if has_project_docs(project_dir):
                ingest_project_docs(store, project_dir, project=project)
                project_memories = store.get_by_project(project, limit=limit)
                shared_memories = store.get_shared_sections(
                    project, limit=min(limit, 10)
                )
            if not project_memories and not shared_memories:
                return

        output = _build_recall_output(
            project, project_memories, shared_memories, budget
        )
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


INSTRUCTION_LINE = (
    "At the start of every session and after every conversation compaction, "
    "call mem_recall() to load cross-project context from crossmem."
)
INSTRUCTION_MARKER = "<!-- crossmem-instruction -->"


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
    """Add 'call mem_recall' instruction to Copilot and Gemini configs.

    Adds a one-line instruction to each tool's config file so the LLM
    calls mem_recall at session start. Claude Code uses install-hook
    instead (deterministic, no LLM decision).

    Target files:
        .github/copilot-instructions.md  (current project)
        ~/.gemini/GEMINI.md              (global)
    """
    targets = {
        "Copilot": Path.cwd() / ".github" / "copilot-instructions.md",
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
def install_hook(uninstall: bool, dry_run: bool) -> None:
    """Add a SessionStart hook to Claude Code settings.

    Installs a hook that automatically loads crossmem memories
    at the start of every Claude Code session. The hook runs
    `crossmem recall` and injects the output as context.

    To remove:

        crossmem install-hook --uninstall
    """
    settings_path = _claude_settings_path()
    settings = _read_settings(settings_path)

    hooks = settings.get("hooks", {})
    session_start = hooks.get("SessionStart", [])

    existing_idx = None
    for i, entry in enumerate(session_start):
        matcher = entry.get("matcher", "")
        hooks_list = entry.get("hooks", [])
        has_crossmem_cmd = any("crossmem recall" in h.get("command", "") for h in hooks_list)
        if matcher in (HOOK_MATCHER, HOOK_MATCHER_LEGACY) or has_crossmem_cmd:
            existing_idx = i
            break

    if uninstall:
        if existing_idx is not None:
            if dry_run:
                click.echo(f"Would remove crossmem hook from {settings_path}")
                return
            session_start.pop(existing_idx)
            if not session_start:
                del hooks["SessionStart"]
            if not hooks:
                del settings["hooks"]
            _write_settings(settings_path, settings)
            click.echo("Removed crossmem hook from Claude Code settings.")
        else:
            click.echo("No crossmem hook found in Claude Code settings.")
        return

    crossmem_bin = _find_crossmem_bin()

    hook_entry = {
        "matcher": HOOK_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": f"{crossmem_bin} recall",
            }
        ],
    }

    if dry_run:
        action = "update" if existing_idx is not None else "add"
        click.echo(f"Would {action} in {settings_path}:\n")
        click.echo(json.dumps({"hooks": {"SessionStart": [hook_entry]}}, indent=2))
        return

    if existing_idx is not None:
        session_start[existing_idx] = hook_entry
        click.echo("Updated crossmem hook in Claude Code settings.")
    else:
        session_start.append(hook_entry)
        click.echo("Installed crossmem hook in Claude Code settings.")

    hooks["SessionStart"] = session_start
    settings["hooks"] = hooks
    _write_settings(settings_path, settings)
    click.echo(f"  Hook: {crossmem_bin} recall")
    click.echo(f"  Settings: {settings_path}")
    click.echo("\nMemories will load automatically at every session start.")
