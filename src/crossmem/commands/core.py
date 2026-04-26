"""Core CRUD commands: ingest, search, forget, update, save, stats, init, graph, serve."""

import tempfile
from pathlib import Path

import click

from crossmem.benchmark import default_benchmark_cases, run_benchmark, seed_benchmark_memories
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
    if not query or not query.strip():
        click.echo("Query cannot be empty. Use `crossmem recall` to list all memories.")
        return

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


@click.command(name="benchmark")
@click.option("-k", "--limit", default=5, show_default=True, help="Top-K results per test case")
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Use strict FTS search only (no synonym expansion)",
)
def benchmark(limit: int, strict: bool) -> None:
    """Run a deterministic recall benchmark against v1.0.0 search behavior."""
    if limit < 1:
        click.echo("--limit must be >= 1")
        return

    with tempfile.TemporaryDirectory(prefix="crossmem-benchmark-") as tmpdir:
        db_path = Path(tmpdir) / "benchmark.db"
        store = MemoryStore(db_path=db_path)
        try:
            seed_benchmark_memories(store)
            report = run_benchmark(
                store,
                cases=default_benchmark_cases(),
                limit=limit,
                expanded=not strict,
            )
        finally:
            store.close()

    mode = "strict" if strict else "expanded"
    click.echo(f"Recall benchmark suite (mode={mode}, k={limit})")
    click.echo(f"Cases: {report.total_cases}")
    click.echo(f"Recall@{limit}: {report.recall_at_k:.2f}")
    click.echo(f"Precision@{limit}: {report.precision_at_k:.2f}")
    click.echo(f"MRR: {report.mrr:.2f}")
    click.echo(f"Noise rate: {report.noise_rate:.2f}\n")

    failed = [r for r in report.case_results if not r.recall_hit]
    if not failed:
        click.echo("All cases passed.")
        return

    click.echo("Failed cases:")
    for result in failed:
        click.echo(f'- {result.case_id}: "{result.query}" (relevant={result.relevant})')


@click.group(invoke_without_command=True)
@click.option(
    "--backfill",
    "do_backfill",
    is_flag=True,
    default=False,
    help="Expand keywords for all memories with no keyword expansion yet.",
)
@click.pass_context
def synonyms(ctx: click.Context, do_backfill: bool) -> None:
    """Manage synonym groups for expanded search.

    Examples:
        crossmem synonyms list
        crossmem synonyms add deploy ship
        crossmem synonyms backfill
        crossmem synonyms --backfill
    """
    if do_backfill:
        store = MemoryStore()
        try:
            count = store.backfill_keywords()
            click.echo(f"Backfilled keywords for {count} memories.")
        finally:
            store.close()
    elif ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@synonyms.command(name="list")
@click.option(
    "--source",
    type=click.Choice(["seed", "user", "learned"], case_sensitive=False),
    default=None,
    help="Filter synonym pairs by provenance source.",
)
def synonyms_list(source: str | None) -> None:
    """List synonym groups, optionally filtered by provenance source."""
    store = MemoryStore()
    try:
        if source is None:
            groups = store.list_synonyms()
        else:
            groups_with_source = store.list_synonyms_with_source(source=source)
            groups = {
                canonical: [term for term, _ in terms]
                for canonical, terms in groups_with_source.items()
            }
        if not groups:
            click.echo("No synonyms defined.")
            return
        for canonical, terms in sorted(groups.items()):
            click.echo(f"{canonical}: {', '.join(sorted(terms))}")
    finally:
        store.close()


@synonyms.command(name="add")
@click.argument("canonical")
@click.argument("term")
def synonyms_add(canonical: str, term: str) -> None:
    """Add a synonym pair to a group.

    Examples:
        crossmem synonyms add deploy ship
        crossmem synonyms add auth token
    """
    store = MemoryStore()
    try:
        store.add_synonym(canonical, term)
        click.echo(f"Added: {canonical} ↔ {term}")
    finally:
        store.close()


@synonyms.command(name="remove")
@click.argument("canonical")
@click.argument("term")
def synonyms_remove(canonical: str, term: str) -> None:
    """Remove a synonym pair.

    Examples:
        crossmem synonyms remove deploy ship
    """
    store = MemoryStore()
    try:
        removed = store.remove_synonym(canonical, term)
        if removed:
            click.echo(f"Removed: {canonical} ↔ {term}")
        else:
            click.echo(f"Not found: {canonical} ↔ {term}")
    finally:
        store.close()


@synonyms.command(name="learn")
@click.option(
    "--max-df-ratio",
    default=0.5,
    show_default=True,
    help="Ignore tokens appearing in more than this fraction of memories.",
)
@click.option(
    "--min-df",
    default=3,
    show_default=True,
    help="Minimum document frequency for a token to qualify.",
)
@click.option(
    "--min-jaccard",
    default=0.3,
    show_default=True,
    help="Minimum Jaccard similarity to add a synonym pair.",
)
def synonyms_learn(max_df_ratio: float, min_df: int, min_jaccard: float) -> None:
    """Mine co-occurrence synonyms from memory content.

    Examples:
        crossmem synonyms learn
        crossmem synonyms learn --min-jaccard 0.2
    """
    store = MemoryStore()
    try:
        added = store.learn_synonyms(
            max_df_ratio=max_df_ratio,
            min_df=min_df,
            min_jaccard=min_jaccard,
        )
        if added:
            click.echo(f"Learned {added} new synonym pair(s).")
        else:
            click.echo("No new synonym pairs found.")
    finally:
        store.close()


@synonyms.command(name="backfill")
def synonyms_backfill() -> None:
    """Expand keywords for all memories that have no keyword expansion yet."""
    store = MemoryStore()
    try:
        count = store.backfill_keywords()
        click.echo(f"Backfilled keywords for {count} memories.")
    finally:
        store.close()


@click.command()
@click.option("-p", "--project", default=None, help="Project name (auto-detected from cwd/git)")
@click.option(
    "--path",
    "project_path",
    default=None,
    type=click.Path(exists=True),
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
            click.echo(f"Initialized '{project}': {added} new memories ({total} total)")
    finally:
        store.close()
