"""CLI interface for crossmem."""

import click

from crossmem.ingest import ingest_claude_memory, ingest_gemini_memory
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
    from crossmem.sync import watch

    watch(interval=interval, project=project)


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
