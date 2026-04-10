"""Tests for CLI commands (recall, install-hook, install-instructions, init)."""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from crossmem.cli import INSTRUCTION_MARKER, _source_tier, main
from crossmem.store import MemoryStore


class TestSourceTier:
    def test_mem_save_is_tier_0(self) -> None:
        assert _source_tier("mcp:mem_save") == 0

    def test_cli_save_is_tier_0(self) -> None:
        assert _source_tier("cli:save") == 0

    def test_ingested_file_is_tier_1(self) -> None:
        assert _source_tier("/home/user/.claude/projects/x/memory/MEMORY.md") == 1

    def test_init_claude_md_is_tier_2(self) -> None:
        assert _source_tier("init:CLAUDE.md") == 2

    def test_init_copilot_instructions_is_tier_2(self) -> None:
        assert _source_tier("init:.github/copilot-instructions.md") == 2

    def test_init_contributing_is_tier_3(self) -> None:
        assert _source_tier("init:CONTRIBUTING.md") == 3

    def test_init_architecture_is_tier_4(self) -> None:
        assert _source_tier("init:ARCHITECTURE.md") == 4

    def test_init_readme_is_tier_5(self) -> None:
        assert _source_tier("init:README.md") == 5

    def test_init_docs_subdir(self) -> None:
        assert _source_tier("init:docs/ARCHITECTURE.md") == 4


class TestRecall:
    def test_outputs_project_memories(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        store.add("Use retry with exponential backoff", "file.md", "backend-api", "Patterns")
        store.add("JWT tokens expire after 1 hour", "file.md", "backend-api", "Security")
        store.close()

        runner = CliRunner()
        with patch("crossmem.cli.MemoryStore", return_value=MemoryStore(db_path=tmp_path / "test.db")):
            result = runner.invoke(main, ["recall", "-p", "backend-api"])

        assert result.exit_code == 0
        assert "backend-api" in result.output
        assert "retry" in result.output
        assert "JWT" in result.output

    def test_no_memories_no_docs_silent(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        store.close()

        empty_dir = tmp_path / "empty-proj"
        empty_dir.mkdir()

        runner = CliRunner()
        with (
            patch("crossmem.cli.MemoryStore", return_value=MemoryStore(db_path=tmp_path / "test.db")),
            patch("crossmem.cli.os.getcwd", return_value=str(empty_dir)),
        ):
            result = runner.invoke(main, ["recall", "-p", "nonexistent"])

        assert result.exit_code == 0
        assert result.output.strip() == ""

    def test_auto_detects_project(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        store.add("FastAPI backend pattern", "file.md", "backend-api", "Architecture")
        store.close()

        runner = CliRunner()
        with (
            patch("crossmem.cli.MemoryStore", return_value=MemoryStore(db_path=tmp_path / "test.db")),
            patch("crossmem.cli.os.getcwd", return_value="/Users/foo/backend-api"),
        ):
            result = runner.invoke(main, ["recall"])

        assert result.exit_code == 0
        assert "backend-api" in result.output

    def test_unknown_project_no_docs_silent(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        store.add("something", "file.md", "other-project", "")
        store.close()

        empty_dir = tmp_path / "no-docs"
        empty_dir.mkdir()

        runner = CliRunner()
        with (
            patch("crossmem.cli.MemoryStore", return_value=MemoryStore(db_path=tmp_path / "test.db")),
            patch("crossmem.cli.os.getcwd", return_value=str(empty_dir)),
        ):
            result = runner.invoke(main, ["recall"])

        assert result.exit_code == 0
        assert result.output.strip() == ""

    def test_auto_init_unknown_project_with_docs(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "new-project"
        project_dir.mkdir()
        (project_dir / "README.md").write_text(
            "# My Project\nA project with enough content to pass the filter.\n"
        )

        runner = CliRunner()
        with (
            patch("crossmem.cli.MemoryStore", return_value=MemoryStore(db_path=tmp_path / "test.db")),
            patch("crossmem.cli.os.getcwd", return_value=str(project_dir)),
        ):
            result = runner.invoke(main, ["recall"])

        assert result.exit_code == 0
        # Should auto-init and return memories, not nudge
        assert "My Project" in result.output
        assert "crossmem init" not in result.output

    def test_auto_init_known_project_no_memories(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "my-proj"
        project_dir.mkdir()
        (project_dir / "README.md").write_text(
            "# My Project\nA project with enough content to pass the filter.\n"
        )

        runner = CliRunner()
        with (
            patch("crossmem.cli.MemoryStore", return_value=MemoryStore(db_path=tmp_path / "test.db")),
            patch("crossmem.cli.os.getcwd", return_value=str(project_dir)),
        ):
            result = runner.invoke(main, ["recall", "-p", "my-proj"])

        assert result.exit_code == 0
        # Should auto-init and return memories
        assert "My Project" in result.output

    def test_budget_limits_output(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        for i in range(20):
            store.add(f"Memory number {i} with enough content to pass the filter", "file.md", "big-project", "")
        store.close()

        runner = CliRunner()
        with patch("crossmem.cli.MemoryStore", return_value=MemoryStore(db_path=tmp_path / "test.db")):
            result = runner.invoke(main, ["recall", "-p", "big-project", "--budget", "300"])

        assert result.exit_code == 0
        assert len(result.output) <= 350  # small margin for trailing newline
        assert result.output.count("Memory number") < 20

    def test_includes_sections(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        store.add("Always use middleware for auth", "file.md", "backend-api", "Security")
        store.close()

        runner = CliRunner()
        with patch("crossmem.cli.MemoryStore", return_value=MemoryStore(db_path=tmp_path / "test.db")):
            result = runner.invoke(main, ["recall", "-p", "backend-api"])

        assert result.exit_code == 0
        assert "[Security]" in result.output

    def test_tiered_ordering_mem_save_first(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        store.add("README intro about the project and its goals", "init:README.md", "proj", "About")
        store.add("Always validate JWT tokens before processing", "mcp:mem_save", "proj", "Security")
        store.add("Use structured logging with correlation IDs", "MEMORY.md", "proj", "Patterns")
        store.close()

        runner = CliRunner()
        with patch("crossmem.cli.MemoryStore", return_value=MemoryStore(db_path=tmp_path / "test.db")):
            result = runner.invoke(main, ["recall", "-p", "proj", "--budget", "5000"])

        lines = [x for x in result.output.split("\n") if x.startswith("- ")]
        assert "JWT" in lines[0]  # mem_save = tier 0
        assert "logging" in lines[1]  # ingested = tier 1
        assert "README" in lines[2]  # init:README = tier 5

    def test_tiered_ordering_claude_md_before_readme(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        store.add("Project description from the README file", "init:README.md", "proj", "About")
        store.add("Always run ruff before committing code changes", "init:CLAUDE.md", "proj", "Rules")
        store.close()

        runner = CliRunner()
        with patch("crossmem.cli.MemoryStore", return_value=MemoryStore(db_path=tmp_path / "test.db")):
            result = runner.invoke(main, ["recall", "-p", "proj", "--budget", "5000"])

        lines = [x for x in result.output.split("\n") if x.startswith("- ")]
        assert "ruff" in lines[0]  # CLAUDE.md = tier 2
        assert "README" in lines[1]  # README.md = tier 5

    def test_budget_prioritizes_curated_over_docs(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        store.add("Critical security pattern for all services", "mcp:mem_save", "proj", "Security")
        store.add("Long README content that takes up lots of space in the budget", "init:README.md", "proj", "About")
        store.close()

        runner = CliRunner()
        with patch("crossmem.cli.MemoryStore", return_value=MemoryStore(db_path=tmp_path / "test.db")):
            result = runner.invoke(main, ["recall", "-p", "proj", "--budget", "150"])

        assert "security" in result.output.lower()
        # README might be truncated by budget


class TestInstallHook:
    def test_installs_hook(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"

        runner = CliRunner()
        with (
            patch("crossmem.cli._claude_settings_path", return_value=settings_path),
            patch("crossmem.cli._find_crossmem_bin", return_value="/usr/local/bin/crossmem"),
        ):
            result = runner.invoke(main, ["install-hook"])

        assert result.exit_code == 0
        assert "Installed" in result.output

        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings
        assert "SessionStart" in settings["hooks"]
        hook = settings["hooks"]["SessionStart"][0]
        assert hook["matcher"] == "startup|compact|resume"
        assert hook["hooks"][0]["command"] == "/usr/local/bin/crossmem recall"

    def test_merges_with_existing_settings(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({
            "permissions": {"allow": ["Bash(git:*)"]},
            "model": "claude-opus-4-6",
        }))

        runner = CliRunner()
        with (
            patch("crossmem.cli._claude_settings_path", return_value=settings_path),
            patch("crossmem.cli._find_crossmem_bin", return_value="crossmem"),
        ):
            result = runner.invoke(main, ["install-hook"])

        assert result.exit_code == 0
        settings = json.loads(settings_path.read_text())
        assert settings["permissions"] == {"allow": ["Bash(git:*)"]}
        assert settings["model"] == "claude-opus-4-6"
        assert "SessionStart" in settings["hooks"]

    def test_merges_with_existing_hooks(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [{"matcher": "other", "hooks": []}],
                "SessionStart": [{"matcher": "existing", "hooks": []}],
            }
        }))

        runner = CliRunner()
        with (
            patch("crossmem.cli._claude_settings_path", return_value=settings_path),
            patch("crossmem.cli._find_crossmem_bin", return_value="crossmem"),
        ):
            result = runner.invoke(main, ["install-hook"])

        assert result.exit_code == 0
        settings = json.loads(settings_path.read_text())
        assert len(settings["hooks"]["SessionStart"]) == 2
        assert settings["hooks"]["PreToolUse"][0]["matcher"] == "other"

    def test_updates_existing_hook(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({
            "hooks": {
                "SessionStart": [
                    {"matcher": "startup", "hooks": [{"type": "command", "command": "crossmem recall"}]},
                ]
            }
        }))

        runner = CliRunner()
        with (
            patch("crossmem.cli._claude_settings_path", return_value=settings_path),
            patch("crossmem.cli._find_crossmem_bin", return_value="/new/crossmem"),
        ):
            result = runner.invoke(main, ["install-hook"])

        assert result.exit_code == 0
        assert "Updated" in result.output
        settings = json.loads(settings_path.read_text())
        assert len(settings["hooks"]["SessionStart"]) == 1
        assert settings["hooks"]["SessionStart"][0]["matcher"] == "startup|compact|resume"
        assert settings["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "/new/crossmem recall"

    def test_migrates_legacy_matcher(self, tmp_path: Path) -> None:
        """Legacy installs used matcher='crossmem-recall' which never fired."""
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({
            "hooks": {
                "SessionStart": [
                    {"matcher": "crossmem-recall", "hooks": [{"type": "command", "command": "old-crossmem recall"}]},
                ]
            }
        }))

        runner = CliRunner()
        with (
            patch("crossmem.cli._claude_settings_path", return_value=settings_path),
            patch("crossmem.cli._find_crossmem_bin", return_value="crossmem"),
        ):
            result = runner.invoke(main, ["install-hook"])

        assert result.exit_code == 0
        assert "Updated" in result.output
        settings = json.loads(settings_path.read_text())
        assert len(settings["hooks"]["SessionStart"]) == 1
        # Migrated to correct matcher
        assert settings["hooks"]["SessionStart"][0]["matcher"] == "startup|compact|resume"

    def test_uninstall_removes_hook(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({
            "hooks": {
                "SessionStart": [
                    {"matcher": "crossmem-recall", "hooks": [{"type": "command", "command": "crossmem recall"}]},
                    {"matcher": "other-hook", "hooks": []},
                ]
            }
        }))

        runner = CliRunner()
        with patch("crossmem.cli._claude_settings_path", return_value=settings_path):
            result = runner.invoke(main, ["install-hook", "--uninstall"])

        assert result.exit_code == 0
        assert "Removed" in result.output
        settings = json.loads(settings_path.read_text())
        assert len(settings["hooks"]["SessionStart"]) == 1
        assert settings["hooks"]["SessionStart"][0]["matcher"] == "other-hook"

    def test_uninstall_cleans_empty_hooks(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({
            "hooks": {
                "SessionStart": [
                    {"matcher": "crossmem-recall", "hooks": []},
                ]
            }
        }))

        runner = CliRunner()
        with patch("crossmem.cli._claude_settings_path", return_value=settings_path):
            result = runner.invoke(main, ["install-hook", "--uninstall"])

        assert result.exit_code == 0
        settings = json.loads(settings_path.read_text())
        assert "hooks" not in settings

    def test_uninstall_no_hook_found(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"model": "opus"}))

        runner = CliRunner()
        with patch("crossmem.cli._claude_settings_path", return_value=settings_path):
            result = runner.invoke(main, ["install-hook", "--uninstall"])

        assert result.exit_code == 0
        assert "No crossmem hook found" in result.output

    def test_dry_run_install(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"

        runner = CliRunner()
        with (
            patch("crossmem.cli._claude_settings_path", return_value=settings_path),
            patch("crossmem.cli._find_crossmem_bin", return_value="crossmem"),
        ):
            result = runner.invoke(main, ["install-hook", "--dry-run"])

        assert result.exit_code == 0
        assert "Would add" in result.output
        assert "startup" in result.output
        assert not settings_path.exists()

    def test_dry_run_uninstall(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({
            "hooks": {"SessionStart": [{"matcher": "crossmem-recall", "hooks": []}]}
        }))

        runner = CliRunner()
        with patch("crossmem.cli._claude_settings_path", return_value=settings_path):
            result = runner.invoke(main, ["install-hook", "--uninstall", "--dry-run"])

        assert result.exit_code == 0
        assert "Would remove" in result.output
        # File should be unchanged
        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings

    def test_malformed_json_error(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text("{broken json,,}")

        runner = CliRunner()
        with patch("crossmem.cli._claude_settings_path", return_value=settings_path):
            result = runner.invoke(main, ["install-hook"])

        assert result.exit_code != 0
        assert "Malformed JSON" in result.output


class TestInstallInstructions:
    def test_adds_to_copilot_instructions(self, tmp_path: Path) -> None:
        copilot_path = tmp_path / ".github" / "copilot-instructions.md"

        runner = CliRunner()
        with (
            patch("crossmem.cli.Path.cwd", return_value=tmp_path),
            patch("crossmem.cli.Path.home", return_value=tmp_path),
        ):
            result = runner.invoke(main, ["install-instructions"])

        assert result.exit_code == 0
        assert "Added" in result.output
        assert copilot_path.exists()
        assert INSTRUCTION_MARKER in copilot_path.read_text()
        assert "mem_recall" in copilot_path.read_text()

    def test_preserves_existing_content(self, tmp_path: Path) -> None:
        copilot_path = tmp_path / ".github" / "copilot-instructions.md"
        copilot_path.parent.mkdir(parents=True)
        copilot_path.write_text("# Existing rules\nAlways use TypeScript.\n")

        runner = CliRunner()
        with (
            patch("crossmem.cli.Path.cwd", return_value=tmp_path),
            patch("crossmem.cli.Path.home", return_value=tmp_path),
        ):
            result = runner.invoke(main, ["install-instructions"])

        assert result.exit_code == 0
        content = copilot_path.read_text()
        assert "Existing rules" in content
        assert "TypeScript" in content
        assert "mem_recall" in content

    def test_idempotent(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with (
            patch("crossmem.cli.Path.cwd", return_value=tmp_path),
            patch("crossmem.cli.Path.home", return_value=tmp_path),
        ):
            runner.invoke(main, ["install-instructions"])
            result = runner.invoke(main, ["install-instructions"])

        assert result.exit_code == 0
        assert "already present" in result.output

    def test_uninstall(self, tmp_path: Path) -> None:
        copilot_path = tmp_path / ".github" / "copilot-instructions.md"

        runner = CliRunner()
        with (
            patch("crossmem.cli.Path.cwd", return_value=tmp_path),
            patch("crossmem.cli.Path.home", return_value=tmp_path),
        ):
            runner.invoke(main, ["install-instructions"])
            result = runner.invoke(main, ["install-instructions", "--uninstall"])

        assert result.exit_code == 0
        assert "Removed" in result.output
        assert INSTRUCTION_MARKER not in copilot_path.read_text()

    def test_dry_run(self, tmp_path: Path) -> None:
        copilot_path = tmp_path / ".github" / "copilot-instructions.md"

        runner = CliRunner()
        with (
            patch("crossmem.cli.Path.cwd", return_value=tmp_path),
            patch("crossmem.cli.Path.home", return_value=tmp_path),
        ):
            result = runner.invoke(main, ["install-instructions", "--dry-run"])

        assert result.exit_code == 0
        assert "would add" in result.output
        assert not copilot_path.exists()

    def test_adds_to_gemini(self, tmp_path: Path) -> None:
        gemini_path = tmp_path / ".gemini" / "GEMINI.md"

        runner = CliRunner()
        with (
            patch("crossmem.cli.Path.cwd", return_value=tmp_path),
            patch("crossmem.cli.Path.home", return_value=tmp_path),
        ):
            result = runner.invoke(main, ["install-instructions"])

        assert result.exit_code == 0
        assert gemini_path.exists()
        assert "mem_recall" in gemini_path.read_text()


class TestInit:
    def test_indexes_readme(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "my-api"
        project_dir.mkdir()
        (project_dir / "README.md").write_text(
            "# My API\nA REST API built with FastAPI and PostgreSQL.\n"
        )

        runner = CliRunner()
        with patch("crossmem.cli.MemoryStore", return_value=MemoryStore(db_path=tmp_path / "test.db")):
            result = runner.invoke(main, ["init", "--path", str(project_dir)])

        assert result.exit_code == 0
        assert "Initialized" in result.output
        assert "1 new memories" in result.output

    def test_explicit_project_name(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "# Overview\nThis project handles payment processing.\n"
        )

        db_path = tmp_path / "test.db"
        runner = CliRunner()
        with patch("crossmem.cli.MemoryStore", return_value=MemoryStore(db_path=db_path)):
            result = runner.invoke(main, ["init", "-p", "payments", "--path", str(tmp_path)])

        assert result.exit_code == 0
        assert "'payments'" in result.output
        # Verify with a fresh store (init closes the original)
        store = MemoryStore(db_path=db_path)
        memories = store.get_by_project("payments")
        assert len(memories) == 1
        store.close()

    def test_no_docs_found(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        runner = CliRunner()
        with patch("crossmem.cli.MemoryStore", return_value=MemoryStore(db_path=tmp_path / "test.db")):
            result = runner.invoke(main, ["init", "--path", str(empty_dir)])

        assert result.exit_code == 0
        assert "No documentation files found" in result.output

    def test_idempotent(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "# Docs\nComprehensive documentation for the project.\n"
        )

        db_path = tmp_path / "test.db"
        runner = CliRunner()
        with patch("crossmem.cli.MemoryStore", return_value=MemoryStore(db_path=db_path)):
            result1 = runner.invoke(main, ["init", "-p", "proj", "--path", str(tmp_path)])
        with patch("crossmem.cli.MemoryStore", return_value=MemoryStore(db_path=db_path)):
            result2 = runner.invoke(main, ["init", "-p", "proj", "--path", str(tmp_path)])

        assert "1 new memories" in result1.output
        assert "already up to date" in result2.output

    def test_multiple_docs(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "# About\nA Python library for data validation.\n"
        )
        (tmp_path / "CONTRIBUTING.md").write_text(
            "# Contributing\nFork the repo and submit a pull request.\n"
        )

        runner = CliRunner()
        with patch("crossmem.cli.MemoryStore", return_value=MemoryStore(db_path=tmp_path / "test.db")):
            result = runner.invoke(main, ["init", "-p", "lib", "--path", str(tmp_path)])

        assert result.exit_code == 0
        assert "2 new memories" in result.output
