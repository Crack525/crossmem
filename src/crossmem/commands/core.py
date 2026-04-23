"""Core CRUD commands: ingest, search, forget, update, save, stats, init, graph, serve."""

from pathlib import Path

import click

from crossmem.ingest import (
    ingest_claude_memory,
    ingest_copilot_memory,
    ingest_gemini_memory,
    ingest_project_docs,
)
from crossmem.store import DEFAULT_DB_PATH, MemoryStore


@click.command()
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


@click.command()
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


@click.command()
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


@click.command()
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


@click.command()
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


@click.command()
@click.option("--port", default=8765, help="Port for local server")
def graph(port: int) -> None:
    """Visualize the knowledge graph in your browser."""
    from crossmem.graph import serve_graph

    store = MemoryStore()
    if store.count() == 0:
        click.echo("No memories yet. Run: crossmem ingest")
        store.close()
        return
    serve_graph(store, port=port)


@click.command()
def serve() -> None:
    """Start the MCP server (stdio transport)."""
    from crossmem.server import main as serve_main

    serve_main()


@click.command()
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


@click.command()
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
