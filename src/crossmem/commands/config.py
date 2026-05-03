"""crossmem config — get/set user configuration."""

import click

from crossmem.store import DEFAULT_DB_PATH, MemoryStore

_VALID_VALUES: dict[str, tuple[str, ...]] = {
    "search-mode": ("fts5", "embeddings", "hybrid"),
}

_DESCRIPTIONS = {
    "search-mode": (
        "Search backend: fts5 (default, keyword), embeddings (semantic), hybrid (both).\n"
        "  embeddings and hybrid require: pip install 'crossmem[embeddings]'"
    ),
}


@click.group("config")
def config() -> None:
    """Get or set crossmem configuration."""


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set a configuration value.

    Example: crossmem config set search-mode hybrid
    """
    if key not in _VALID_VALUES:
        valid_keys = ", ".join(sorted(_VALID_VALUES))
        raise click.UsageError(f"Unknown key '{key}'. Valid keys: {valid_keys}")
    allowed = _VALID_VALUES[key]
    if value not in allowed:
        raise click.UsageError(
            f"Invalid value '{value}' for '{key}'. Allowed: {', '.join(allowed)}"
        )
    store = MemoryStore(DEFAULT_DB_PATH)
    try:
        store.set_config(key, value)
        click.echo(f"Set {key} = {value}")
        if key == "search-mode" and value in ("embeddings", "hybrid"):
            if not store._vec_available:
                click.echo(
                    "  Note: embeddings backend not active. "
                    "Install with: pip install 'crossmem[embeddings]'\n"
                    "  Then run: crossmem config backfill-embeddings"
                )
            else:
                unembedded = store.db.execute(
                    "SELECT COUNT(*) FROM memories WHERE id NOT IN (SELECT rowid FROM vec_memories)"
                ).fetchone()[0]
                if unembedded:
                    click.echo(
                        f"  {unembedded} existing memories need embedding. "
                        "Run: crossmem config backfill-embeddings"
                    )
    finally:
        store.close()


@config.command("get")
@click.argument("key", required=False)
def config_get(key: str | None = None) -> None:
    """Show current configuration (or a specific key)."""
    store = MemoryStore(DEFAULT_DB_PATH)
    try:
        if key:
            if key not in _VALID_VALUES:
                valid_keys = ", ".join(sorted(_VALID_VALUES))
                raise click.UsageError(f"Unknown key '{key}'. Valid keys: {valid_keys}")
            value = store.get_config(key, _default_for(key))
            click.echo(f"{key} = {value}")
        else:
            for k in sorted(_VALID_VALUES):
                value = store.get_config(k, _default_for(k))
                click.echo(f"{k} = {value}")
                if k in _DESCRIPTIONS:
                    for line in _DESCRIPTIONS[k].splitlines():
                        click.echo(f"  {line}")
            vec_status = "active" if store._vec_available else "not installed"
            click.echo(f"\nembeddings backend: {vec_status}")
    finally:
        store.close()


@config.command("backfill-embeddings")
def backfill_embeddings() -> None:
    """Embed all memories that don't have a stored vector yet."""
    store = MemoryStore(DEFAULT_DB_PATH)
    try:
        if not store._vec_available:
            click.echo(
                "Embeddings backend not active. Install with: pip install 'crossmem[embeddings]'"
            )
            return
        click.echo("Backfilling embeddings for un-embedded memories…")
        count = store.backfill_embeddings()
        click.echo(f"Done — {count} memories embedded.")
    finally:
        store.close()


def _default_for(key: str) -> str:
    defaults = {"search-mode": "fts5"}
    return defaults.get(key, "")
