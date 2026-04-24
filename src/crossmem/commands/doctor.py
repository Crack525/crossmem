"""Doctor command — diagnose crossmem installation health."""

import json
import shutil
import sqlite3
import sys
from pathlib import Path

import click

from crossmem.store import DEFAULT_DB_PATH


def _check_binary() -> tuple[str, str | None]:
    """Check if crossmem binary is resolvable and executable."""
    venv_bin = Path(sys.executable).parent / "crossmem"
    if venv_bin.exists():
        return "ok", str(venv_bin)
    which = shutil.which("crossmem")
    if which:
        return "ok", which
    return "warn", None


def _check_database() -> tuple[str, str]:
    """Check database exists, is valid SQLite, and has memories."""
    if not DEFAULT_DB_PATH.exists():
        return "fail", f"Database not found: {DEFAULT_DB_PATH}"
    try:
        conn = sqlite3.connect(str(DEFAULT_DB_PATH), timeout=5)
        conn.row_factory = sqlite3.Row
        count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        conn.close()
    except Exception as e:
        return "fail", f"Database error: {e}"
    if count == 0:
        return "warn", f"Database exists but is empty ({DEFAULT_DB_PATH})"
    return "ok", f"{count} memories in {DEFAULT_DB_PATH}"


def _check_claude_hook() -> tuple[str, str]:
    """Check Claude Code hook is installed in settings.json."""
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return "fail", f"Not found: {settings_path}"
    try:
        data = json.loads(settings_path.read_text())
    except json.JSONDecodeError as e:
        return "fail", f"Malformed JSON: {e}"

    hooks = data.get("hooks", {})
    has_recall = False
    has_prompt = False
    for event_hooks in hooks.values():
        if not isinstance(event_hooks, list):
            continue
        for entry in event_hooks:
            if not isinstance(entry, dict):
                continue
            # settings.json structure: [{matcher, hooks: [{type, command}]}]
            inner = entry.get("hooks", [entry])  # fallback: entry itself is the hook
            for hook in inner:
                cmd = hook.get("command", "") if isinstance(hook, dict) else ""
                if "crossmem recall" in cmd:
                    has_recall = True
                if "crossmem prompt-search" in cmd:
                    has_prompt = True

    if has_recall and has_prompt:
        return "ok", "recall + prompt-search hooks present"
    if has_recall:
        return "warn", "recall hook present, prompt-search missing"
    if has_prompt:
        return "warn", "prompt-search present, recall hook missing"
    return "fail", "No crossmem hooks found in Claude settings"


def _check_gemini_instructions() -> tuple[str, str]:
    """Check Gemini instructions contain crossmem marker."""
    gemini_path = Path.home() / ".gemini" / "GEMINI.md"
    if not gemini_path.exists():
        return "skip", f"Not found: {gemini_path}"
    content = gemini_path.read_text()
    if "<!-- crossmem-instruction -->" in content:
        return "ok", "Instruction marker present"
    return "warn", "File exists but crossmem instruction missing"


def _check_fts_integrity() -> tuple[str, str]:
    """Run FTS5 integrity check on the search index."""
    if not DEFAULT_DB_PATH.exists():
        return "skip", "No database"
    try:
        conn = sqlite3.connect(str(DEFAULT_DB_PATH), timeout=5)
        conn.execute("INSERT INTO memories_fts(memories_fts) VALUES ('integrity-check')")
        conn.close()
    except Exception as e:
        return "fail", f"FTS index corrupt: {e}"
    return "ok", "FTS5 index intact"


_STATUS_SYMBOLS = {"ok": "✓", "warn": "!", "fail": "✗", "skip": "-"}


@click.command()
def doctor() -> None:
    """Diagnose crossmem installation health."""
    checks = [
        ("Binary", _check_binary),
        ("Database", _check_database),
        ("Claude hook", _check_claude_hook),
        ("Gemini instructions", _check_gemini_instructions),
        ("FTS index", _check_fts_integrity),
    ]

    results = []
    for name, fn in checks:
        status, detail = fn()
        results.append((name, status, detail))

    click.echo("crossmem doctor\n")
    for name, status, detail in results:
        symbol = _STATUS_SYMBOLS.get(status, "?")
        click.echo(f"  {symbol} {name}: {detail or 'not found'}")

    fails = sum(1 for _, s, _ in results if s == "fail")
    warns = sum(1 for _, s, _ in results if s == "warn")

    click.echo()
    if fails:
        click.echo(f"{fails} issue(s) found. Run: crossmem setup")
    elif warns:
        click.echo(f"{warns} warning(s). Run: crossmem setup")
    else:
        click.echo("All checks passed.")
