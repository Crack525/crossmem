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
            click.echo(f"[{i}] {mem.project} / {mem.section or '(root)'}")
            click.echo(f"    Source: {mem.source_file.split('/')[-1]}")
            click.echo(f"    {mem.snippet}")
            click.echo()
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
