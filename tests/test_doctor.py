"""Tests for the doctor command."""

import json
import sqlite3
from pathlib import Path
from unittest import mock

import pytest
from click.testing import CliRunner

from crossmem.cli import main
from crossmem.commands.doctor import (
    _check_binary,
    _check_claude_hook,
    _check_database,
    _check_fts_integrity,
    _check_gemini_instructions,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestCheckBinary:
    def test_found_in_venv(self, tmp_path: Path) -> None:
        fake_bin = tmp_path / "crossmem"
        fake_bin.touch()
        with mock.patch("crossmem.commands.doctor.sys") as mock_sys:
            mock_sys.executable = str(tmp_path / "python")
            status, detail = _check_binary()
        assert status == "ok"
        assert str(fake_bin) in detail

    def test_found_on_path(self) -> None:
        with (
            mock.patch("crossmem.commands.doctor.sys") as mock_sys,
            mock.patch("crossmem.commands.doctor.shutil.which", return_value="/usr/bin/crossmem"),
        ):
            mock_sys.executable = "/nonexistent/python"
            status, detail = _check_binary()
        assert status == "ok"
        assert detail == "/usr/bin/crossmem"

    def test_not_found(self) -> None:
        with (
            mock.patch("crossmem.commands.doctor.sys") as mock_sys,
            mock.patch("crossmem.commands.doctor.shutil.which", return_value=None),
        ):
            mock_sys.executable = "/nonexistent/python"
            status, _ = _check_binary()
        assert status == "warn"


class TestCheckDatabase:
    def test_db_exists_with_memories(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT)")
        conn.execute("INSERT INTO memories VALUES (1, 'hello')")
        conn.commit()
        conn.close()
        with mock.patch("crossmem.commands.doctor.DEFAULT_DB_PATH", db_path):
            status, detail = _check_database()
        assert status == "ok"
        assert "1 memories" in detail

    def test_db_exists_empty(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT)")
        conn.commit()
        conn.close()
        with mock.patch("crossmem.commands.doctor.DEFAULT_DB_PATH", db_path):
            status, _ = _check_database()
        assert status == "warn"

    def test_db_not_found(self, tmp_path: Path) -> None:
        with mock.patch(
            "crossmem.commands.doctor.DEFAULT_DB_PATH",
            tmp_path / "nonexistent.db",
        ):
            status, _ = _check_database()
        assert status == "fail"

    def test_db_corrupt(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corrupt.db"
        db_path.write_text("not a database")
        with mock.patch("crossmem.commands.doctor.DEFAULT_DB_PATH", db_path):
            status, detail = _check_database()
        assert status == "fail"
        assert "Database error" in detail


class TestCheckClaudeHook:
    def test_both_hooks_present(self, tmp_path: Path) -> None:
        settings = {
            "hooks": {
                "PostToolUse": [{"command": "crossmem recall --project test"}],
                "PreToolUse": [{"command": "crossmem prompt-search --budget 5"}],
            }
        }
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps(settings))
        with mock.patch("crossmem.commands.doctor.Path.home", return_value=tmp_path):
            # settings_path = Path.home() / ".claude" / "settings.json"
            claude_dir = tmp_path / ".claude"
            claude_dir.mkdir()
            (claude_dir / "settings.json").write_text(json.dumps(settings))
            status, detail = _check_claude_hook()
        assert status == "ok"
        assert "recall" in detail
        assert "prompt-search" in detail

    def test_recall_only(self, tmp_path: Path) -> None:
        settings = {
            "hooks": {
                "PostToolUse": [{"command": "crossmem recall --project test"}],
            }
        }
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        with mock.patch("crossmem.commands.doctor.Path.home", return_value=tmp_path):
            status, _ = _check_claude_hook()
        assert status == "warn"

    def test_no_hooks(self, tmp_path: Path) -> None:
        settings = {"hooks": {}}
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        with mock.patch("crossmem.commands.doctor.Path.home", return_value=tmp_path):
            status, _ = _check_claude_hook()
        assert status == "fail"

    def test_settings_missing(self, tmp_path: Path) -> None:
        with mock.patch("crossmem.commands.doctor.Path.home", return_value=tmp_path):
            status, _ = _check_claude_hook()
        assert status == "fail"

    def test_malformed_json(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{bad json")
        with mock.patch("crossmem.commands.doctor.Path.home", return_value=tmp_path):
            status, detail = _check_claude_hook()
        assert status == "fail"
        assert "Malformed" in detail


class TestCheckGeminiInstructions:
    def test_marker_present(self, tmp_path: Path) -> None:
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "GEMINI.md").write_text(
            "# Instructions\n<!-- crossmem-instruction -->\nstuff"
        )
        with mock.patch("crossmem.commands.doctor.Path.home", return_value=tmp_path):
            status, _ = _check_gemini_instructions()
        assert status == "ok"

    def test_file_without_marker(self, tmp_path: Path) -> None:
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "GEMINI.md").write_text("# No marker here")
        with mock.patch("crossmem.commands.doctor.Path.home", return_value=tmp_path):
            status, _ = _check_gemini_instructions()
        assert status == "warn"

    def test_file_missing(self, tmp_path: Path) -> None:
        with mock.patch("crossmem.commands.doctor.Path.home", return_value=tmp_path):
            status, _ = _check_gemini_instructions()
        assert status == "skip"


class TestCheckFtsIntegrity:
    def test_healthy_fts(self, tmp_path: Path) -> None:
        from crossmem.store import MemoryStore

        db_path = tmp_path / "test.db"
        store = MemoryStore(db_path=db_path)
        store.add("test content", "f.md", "proj")
        store.close()
        with mock.patch("crossmem.commands.doctor.DEFAULT_DB_PATH", db_path):
            status, _ = _check_fts_integrity()
        assert status == "ok"

    def test_no_database(self, tmp_path: Path) -> None:
        with mock.patch(
            "crossmem.commands.doctor.DEFAULT_DB_PATH",
            tmp_path / "nonexistent.db",
        ):
            status, _ = _check_fts_integrity()
        assert status == "skip"


class TestDoctorCLI:
    def test_doctor_runs(self, runner: CliRunner, tmp_path: Path) -> None:
        """Doctor command executes and produces output."""
        db_path = tmp_path / "test.db"
        from crossmem.store import MemoryStore

        store = MemoryStore(db_path=db_path)
        store.add("test", "f.md", "proj")
        store.close()

        with (
            mock.patch("crossmem.commands.doctor.DEFAULT_DB_PATH", db_path),
            mock.patch(
                "crossmem.commands.doctor.Path.home",
                return_value=tmp_path,
            ),
        ):
            result = runner.invoke(main, ["doctor"])
        assert result.exit_code == 0
        assert "crossmem doctor" in result.output

    def test_all_checks_shown(self, runner: CliRunner, tmp_path: Path) -> None:
        """All check names appear in output."""
        with (
            mock.patch(
                "crossmem.commands.doctor.DEFAULT_DB_PATH",
                tmp_path / "nonexistent.db",
            ),
            mock.patch(
                "crossmem.commands.doctor.Path.home",
                return_value=tmp_path,
            ),
        ):
            result = runner.invoke(main, ["doctor"])
        assert "Binary" in result.output
        assert "Database" in result.output
        assert "Claude hook" in result.output
        assert "Gemini" in result.output
        assert "FTS" in result.output
