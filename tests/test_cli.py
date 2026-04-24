"""Tests for CLI commands (recall, install-hook, install-instructions, init)."""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from crossmem.cli import main
from crossmem.commands.hooks import (
    COPILOT_CONTENT_MARKER_END,
    COPILOT_CONTENT_MARKER_START,
    INSTRUCTION_MARKER,
    _build_copilot_block,
    _copilot_global_path,
    _inject_copilot_block,
    _parse_block_timestamp,
    _source_tier,
    _strip_copilot_block,
)
from crossmem.store import Memory, MemoryStore, SearchResult


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
        with patch(
            "crossmem.commands.hooks.MemoryStore",
            return_value=MemoryStore(db_path=tmp_path / "test.db"),
        ):
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
            patch(
                "crossmem.commands.hooks.MemoryStore",
                return_value=MemoryStore(db_path=tmp_path / "test.db"),
            ),
            patch("crossmem.commands.hooks.os.getcwd", return_value=str(empty_dir)),
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
            patch(
                "crossmem.commands.hooks.MemoryStore",
                return_value=MemoryStore(db_path=tmp_path / "test.db"),
            ),
            patch("crossmem.commands.hooks.os.getcwd", return_value="/Users/foo/backend-api"),
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
            patch(
                "crossmem.commands.hooks.MemoryStore",
                return_value=MemoryStore(db_path=tmp_path / "test.db"),
            ),
            patch("crossmem.commands.hooks.os.getcwd", return_value=str(empty_dir)),
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
            patch(
                "crossmem.commands.hooks.MemoryStore",
                return_value=MemoryStore(db_path=tmp_path / "test.db"),
            ),
            patch("crossmem.commands.hooks.os.getcwd", return_value=str(project_dir)),
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
            patch(
                "crossmem.commands.hooks.MemoryStore",
                return_value=MemoryStore(db_path=tmp_path / "test.db"),
            ),
            patch("crossmem.commands.hooks.os.getcwd", return_value=str(project_dir)),
        ):
            result = runner.invoke(main, ["recall", "-p", "my-proj"])

        assert result.exit_code == 0
        # Should auto-init and return memories
        assert "My Project" in result.output

    def test_budget_limits_output(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        for i in range(20):
            store.add(
                f"Memory number {i} with enough content to pass the filter",
                "file.md",
                "big-project",
                "",
            )
        store.close()

        runner = CliRunner()
        with patch(
            "crossmem.commands.hooks.MemoryStore",
            return_value=MemoryStore(db_path=tmp_path / "test.db"),
        ):
            result = runner.invoke(main, ["recall", "-p", "big-project", "--budget", "300"])

        assert result.exit_code == 0
        assert len(result.output) <= 350  # small margin for trailing newline
        assert result.output.count("Memory number") < 20

    def test_includes_sections(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        store.add("Always use middleware for auth", "file.md", "backend-api", "Security")
        store.close()

        runner = CliRunner()
        with patch(
            "crossmem.commands.hooks.MemoryStore",
            return_value=MemoryStore(db_path=tmp_path / "test.db"),
        ):
            result = runner.invoke(main, ["recall", "-p", "backend-api"])

        assert result.exit_code == 0
        assert "[Security]" in result.output

    def test_tiered_ordering_mem_save_first(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        store.add("README intro about the project and its goals", "init:README.md", "proj", "About")
        store.add(
            "Always validate JWT tokens before processing", "mcp:mem_save", "proj", "Security"
        )
        store.add("Use structured logging with correlation IDs", "MEMORY.md", "proj", "Patterns")
        store.close()

        runner = CliRunner()
        with patch(
            "crossmem.commands.hooks.MemoryStore",
            return_value=MemoryStore(db_path=tmp_path / "test.db"),
        ):
            result = runner.invoke(main, ["recall", "-p", "proj", "--budget", "5000"])

        lines = [x for x in result.output.split("\n") if x.startswith("- ")]
        assert "JWT" in lines[0]  # mem_save = tier 0
        assert "logging" in lines[1]  # ingested = tier 1
        assert "README" in lines[2]  # init:README = tier 5

    def test_tiered_ordering_claude_md_before_readme(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        store.add("Project description from the README file", "init:README.md", "proj", "About")
        store.add(
            "Always run ruff before committing code changes", "init:CLAUDE.md", "proj", "Rules"
        )
        store.close()

        runner = CliRunner()
        with patch(
            "crossmem.commands.hooks.MemoryStore",
            return_value=MemoryStore(db_path=tmp_path / "test.db"),
        ):
            result = runner.invoke(main, ["recall", "-p", "proj", "--budget", "5000"])

        lines = [x for x in result.output.split("\n") if x.startswith("- ")]
        assert "ruff" in lines[0]  # CLAUDE.md = tier 2
        assert "README" in lines[1]  # README.md = tier 5

    def test_budget_prioritizes_curated_over_docs(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        store.add("Critical security pattern for all services", "mcp:mem_save", "proj", "Security")
        store.add(
            "Long README content that takes up lots of space in the budget",
            "init:README.md",
            "proj",
            "About",
        )
        store.close()

        runner = CliRunner()
        with patch(
            "crossmem.commands.hooks.MemoryStore",
            return_value=MemoryStore(db_path=tmp_path / "test.db"),
        ):
            result = runner.invoke(main, ["recall", "-p", "proj", "--budget", "150"])

        assert "security" in result.output.lower()
        # README might be truncated by budget


class TestInstallHook:
    def test_installs_hook(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"

        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks._claude_settings_path", return_value=settings_path),
            patch(
                "crossmem.commands.hooks._find_crossmem_bin", return_value="/usr/local/bin/crossmem"
            ),
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
        assert "UserPromptSubmit" in settings["hooks"]
        ups_hook = settings["hooks"]["UserPromptSubmit"][0]
        assert ups_hook["hooks"][0]["command"] == "/usr/local/bin/crossmem prompt-search"

    def test_merges_with_existing_settings(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "permissions": {"allow": ["Bash(git:*)"]},
                    "model": "claude-opus-4-6",
                }
            )
        )

        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks._claude_settings_path", return_value=settings_path),
            patch("crossmem.commands.hooks._find_crossmem_bin", return_value="crossmem"),
        ):
            result = runner.invoke(main, ["install-hook"])

        assert result.exit_code == 0
        settings = json.loads(settings_path.read_text())
        assert settings["permissions"] == {"allow": ["Bash(git:*)"]}
        assert settings["model"] == "claude-opus-4-6"
        assert "SessionStart" in settings["hooks"]

    def test_merges_with_existing_hooks(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [{"matcher": "other", "hooks": []}],
                        "SessionStart": [{"matcher": "existing", "hooks": []}],
                    }
                }
            )
        )

        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks._claude_settings_path", return_value=settings_path),
            patch("crossmem.commands.hooks._find_crossmem_bin", return_value="crossmem"),
        ):
            result = runner.invoke(main, ["install-hook"])

        assert result.exit_code == 0
        settings = json.loads(settings_path.read_text())
        assert len(settings["hooks"]["SessionStart"]) == 2
        assert settings["hooks"]["PreToolUse"][0]["matcher"] == "other"

    def test_updates_existing_hook(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "startup",
                                "hooks": [{"type": "command", "command": "crossmem recall"}],
                            },
                        ]
                    }
                }
            )
        )

        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks._claude_settings_path", return_value=settings_path),
            patch("crossmem.commands.hooks._find_crossmem_bin", return_value="/new/crossmem"),
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
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "crossmem-recall",
                                "hooks": [{"type": "command", "command": "old-crossmem recall"}],
                            },
                        ]
                    }
                }
            )
        )

        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks._claude_settings_path", return_value=settings_path),
            patch("crossmem.commands.hooks._find_crossmem_bin", return_value="crossmem"),
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
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "crossmem-recall",
                                "hooks": [{"type": "command", "command": "crossmem recall"}],
                            },
                            {"matcher": "other-hook", "hooks": []},
                        ]
                    }
                }
            )
        )

        runner = CliRunner()
        with patch("crossmem.commands.hooks._claude_settings_path", return_value=settings_path):
            result = runner.invoke(main, ["install-hook", "--uninstall"])

        assert result.exit_code == 0
        assert "Removed" in result.output
        settings = json.loads(settings_path.read_text())
        assert len(settings["hooks"]["SessionStart"]) == 1
        assert settings["hooks"]["SessionStart"][0]["matcher"] == "other-hook"

    def test_uninstall_cleans_empty_hooks(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {"matcher": "crossmem-recall", "hooks": []},
                        ]
                    }
                }
            )
        )

        runner = CliRunner()
        with patch("crossmem.commands.hooks._claude_settings_path", return_value=settings_path):
            result = runner.invoke(main, ["install-hook", "--uninstall"])

        assert result.exit_code == 0
        settings = json.loads(settings_path.read_text())
        assert "hooks" not in settings

    def test_uninstall_no_hook_found(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"model": "opus"}))

        runner = CliRunner()
        with patch("crossmem.commands.hooks._claude_settings_path", return_value=settings_path):
            result = runner.invoke(main, ["install-hook", "--uninstall"])

        assert result.exit_code == 0
        assert "No crossmem hooks found" in result.output

    def test_dry_run_install(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"

        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks._claude_settings_path", return_value=settings_path),
            patch("crossmem.commands.hooks._find_crossmem_bin", return_value="crossmem"),
        ):
            result = runner.invoke(main, ["install-hook", "--dry-run"])

        assert result.exit_code == 0
        assert "Would install" in result.output
        assert "SessionStart" in result.output
        assert "UserPromptSubmit" in result.output
        assert not settings_path.exists()

    def test_dry_run_uninstall(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(
            json.dumps({"hooks": {"SessionStart": [{"matcher": "crossmem-recall", "hooks": []}]}})
        )

        runner = CliRunner()
        with patch("crossmem.commands.hooks._claude_settings_path", return_value=settings_path):
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
        with patch("crossmem.commands.hooks._claude_settings_path", return_value=settings_path):
            result = runner.invoke(main, ["install-hook"])

        assert result.exit_code != 0
        assert "Malformed JSON" in result.output


class TestInstallInstructions:
    def test_adds_to_copilot_instructions(self, tmp_path: Path) -> None:
        """install-instructions no longer writes Copilot — use install-hook --tool copilot."""
        copilot_path = tmp_path / ".github" / "copilot-instructions.md"

        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks.Path.home", return_value=tmp_path),
        ):
            result = runner.invoke(main, ["install-instructions"])

        assert result.exit_code == 0
        # Copilot file is NOT written — use install-hook --tool copilot instead
        assert not copilot_path.exists()

    def test_preserves_existing_content(self, tmp_path: Path) -> None:
        """install-instructions only touches Gemini; existing Copilot content is untouched."""
        copilot_path = tmp_path / ".github" / "copilot-instructions.md"
        copilot_path.parent.mkdir(parents=True)
        copilot_path.write_text("# Existing rules\nAlways use TypeScript.\n")

        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks.Path.home", return_value=tmp_path),
        ):
            result = runner.invoke(main, ["install-instructions"])

        assert result.exit_code == 0
        # Copilot file is unchanged
        content = copilot_path.read_text()
        assert "Existing rules" in content
        assert "TypeScript" in content
        assert "mem_recall" not in content

    def test_idempotent(self, tmp_path: Path) -> None:
        """Running install-instructions twice on Gemini is idempotent."""
        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks.Path.home", return_value=tmp_path),
        ):
            runner.invoke(main, ["install-instructions"])
            result = runner.invoke(main, ["install-instructions"])

        assert result.exit_code == 0
        assert "already present" in result.output

    def test_uninstall(self, tmp_path: Path) -> None:
        gemini_path = tmp_path / ".gemini" / "GEMINI.md"

        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks.Path.home", return_value=tmp_path),
        ):
            runner.invoke(main, ["install-instructions"])
            result = runner.invoke(main, ["install-instructions", "--uninstall"])

        assert result.exit_code == 0
        assert "Removed" in result.output
        assert INSTRUCTION_MARKER not in gemini_path.read_text()

    def test_dry_run(self, tmp_path: Path) -> None:
        gemini_path = tmp_path / ".gemini" / "GEMINI.md"

        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks.Path.home", return_value=tmp_path),
        ):
            result = runner.invoke(main, ["install-instructions", "--dry-run"])

        assert result.exit_code == 0
        assert "would add" in result.output
        assert not gemini_path.exists()

    def test_adds_to_gemini(self, tmp_path: Path) -> None:
        gemini_path = tmp_path / ".gemini" / "GEMINI.md"

        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks.Path.home", return_value=tmp_path),
        ):
            result = runner.invoke(main, ["install-instructions"])

        assert result.exit_code == 0
        assert gemini_path.exists()
        assert "mem_recall" in gemini_path.read_text()


class TestCopilotHookHelpers:
    """Unit tests for Copilot block helpers (_strip, _build, _inject, _path)."""

    def test_build_copilot_block_contains_markers(self) -> None:
        block = _build_copilot_block("# crossmem: myproject\n- some memory")
        assert COPILOT_CONTENT_MARKER_START in block
        assert COPILOT_CONTENT_MARKER_END in block
        assert "some memory" in block

    def test_build_copilot_block_contains_date(self) -> None:
        import datetime

        block = _build_copilot_block("content")
        assert datetime.date.today().isoformat() in block

    def test_strip_copilot_block_removes_injected_section(self) -> None:
        content = (
            "# Human content\n\n"
            f"{COPILOT_CONTENT_MARKER_START} 2026-04-12 -->\n"
            "injected line\n"
            f"{COPILOT_CONTENT_MARKER_END}\n"
        )
        result = _strip_copilot_block(content)
        assert "Human content" in result
        assert "injected line" not in result
        assert COPILOT_CONTENT_MARKER_START not in result
        assert COPILOT_CONTENT_MARKER_END not in result

    def test_strip_copilot_block_no_marker_returns_unchanged(self) -> None:
        content = "# Just human content\nNo markers here.\n"
        result = _strip_copilot_block(content)
        assert "Just human content" in result

    def test_inject_copilot_block_appends_when_no_marker(self, tmp_path: Path) -> None:
        target = tmp_path / "copilot-instructions.md"
        target.write_text("# Existing\nKeep this.\n")
        block = _build_copilot_block("new memory")
        changed = _inject_copilot_block(target, block, dry_run=False)
        assert changed is True
        written = target.read_text()
        assert "Keep this" in written
        assert "new memory" in written

    def test_inject_copilot_block_replaces_existing_block(self, tmp_path: Path) -> None:
        target = tmp_path / "copilot-instructions.md"
        first_block = _build_copilot_block("old memory")
        target.write_text("# Existing\n\n" + first_block)
        second_block = _build_copilot_block("new memory")
        _inject_copilot_block(target, second_block, dry_run=False)
        written = target.read_text()
        assert "old memory" not in written
        assert "new memory" in written
        assert "Existing" in written
        # Only one copy of the end marker
        assert written.count(COPILOT_CONTENT_MARKER_END) == 1

    def test_inject_copilot_block_idempotent(self, tmp_path: Path) -> None:
        target = tmp_path / "copilot-instructions.md"
        block = _build_copilot_block("same memory")
        _inject_copilot_block(target, block, dry_run=False)
        first = target.read_text()
        _inject_copilot_block(target, block, dry_run=False)
        second = target.read_text()
        # Content is the same; changed flag may be False on identical block
        assert first == second

    def test_inject_copilot_block_dry_run_does_not_write(self, tmp_path: Path) -> None:
        target = tmp_path / "copilot-instructions.md"
        block = _build_copilot_block("memory")
        changed = _inject_copilot_block(target, block, dry_run=True)
        assert changed is True
        assert not target.exists()

    def test_inject_copilot_block_creates_parent_dirs(self, tmp_path: Path) -> None:
        target = tmp_path / ".github" / "copilot-instructions.md"
        block = _build_copilot_block("memory")
        _inject_copilot_block(target, block, dry_run=False)
        assert target.exists()

    def test_copilot_global_path_is_absolute(self) -> None:
        path = _copilot_global_path()
        assert path.is_absolute()
        assert "copilot-instructions.md" in str(path)

    def test_copilot_global_path_platform_branches(self) -> None:
        import platform as _platform

        system = _platform.system()
        path = _copilot_global_path()
        if system == "Darwin":
            assert "Application Support" in str(path)
        elif system == "Windows":
            assert "Code" in str(path)
        else:
            assert ".config" in str(path)


class TestInstallHookCopilot:
    """Integration tests for install-hook --tool copilot."""

    FAKE_RECALL = "# crossmem: myproject\n- use retry with backoff\n"

    def test_workspace_injection(self, tmp_path: Path) -> None:
        copilot_path = tmp_path / ".github" / "copilot-instructions.md"
        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks._get_recall_content", return_value=self.FAKE_RECALL),
        ):
            result = runner.invoke(main, ["install-hook", "--tool", "copilot"])

        assert result.exit_code == 0
        assert "Injected" in result.output
        assert copilot_path.exists()
        content = copilot_path.read_text()
        assert COPILOT_CONTENT_MARKER_START in content
        assert COPILOT_CONTENT_MARKER_END in content

    def test_workspace_injection_idempotent(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks._get_recall_content", return_value=self.FAKE_RECALL),
        ):
            runner.invoke(main, ["install-hook", "--tool", "copilot"])
            result = runner.invoke(main, ["install-hook", "--tool", "copilot"])

        assert result.exit_code == 0
        copilot_path = tmp_path / ".github" / "copilot-instructions.md"
        assert copilot_path.read_text().count(COPILOT_CONTENT_MARKER_END) == 1

    def test_workspace_dry_run_does_not_write(self, tmp_path: Path) -> None:
        copilot_path = tmp_path / ".github" / "copilot-instructions.md"
        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks._get_recall_content", return_value=self.FAKE_RECALL),
        ):
            result = runner.invoke(main, ["install-hook", "--tool", "copilot", "--dry-run"])

        assert result.exit_code == 0
        assert "Would write" in result.output
        assert not copilot_path.exists()

    def test_no_memories_prints_message(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks._get_recall_content", return_value=None),
        ):
            result = runner.invoke(main, ["install-hook", "--tool", "copilot"])

        assert result.exit_code == 0
        assert "No memories" in result.output

    def test_uninstall_removes_block(self, tmp_path: Path) -> None:
        copilot_path = tmp_path / ".github" / "copilot-instructions.md"
        copilot_path.parent.mkdir(parents=True)
        block = _build_copilot_block("injected memory")
        copilot_path.write_text("# Keep this\n\n" + block)

        runner = CliRunner()
        with patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path):
            result = runner.invoke(main, ["install-hook", "--tool", "copilot", "--uninstall"])

        assert result.exit_code == 0
        assert "Removed" in result.output
        content = copilot_path.read_text()
        assert "Keep this" in content
        assert COPILOT_CONTENT_MARKER_START not in content

    def test_uninstall_no_block_reports_not_found(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path):
            result = runner.invoke(main, ["install-hook", "--tool", "copilot", "--uninstall"])
        assert result.exit_code == 0
        assert "No crossmem block found" in result.output

    def test_global_flag_targets_user_path(self, tmp_path: Path) -> None:
        fake_global = tmp_path / "prompts" / "copilot-instructions.md"
        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks._copilot_global_path", return_value=fake_global),
            patch("crossmem.commands.hooks._get_recall_content", return_value=self.FAKE_RECALL),
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
        ):
            result = runner.invoke(main, ["install-hook", "--tool", "copilot", "--global"])

        assert result.exit_code == 0
        assert fake_global.exists()
        assert COPILOT_CONTENT_MARKER_START in fake_global.read_text()

    def test_claude_default_still_works(self, tmp_path: Path) -> None:
        """Existing --tool claude (default) behavior is unchanged."""
        settings_path = tmp_path / ".claude" / "settings.json"
        runner = CliRunner()
        with patch("crossmem.commands.hooks._claude_settings_path", return_value=settings_path):
            result = runner.invoke(main, ["install-hook"])
        assert result.exit_code == 0
        assert settings_path.exists()

    def test_if_stale_with_claude_emits_warning(self, tmp_path: Path) -> None:
        """--if-stale is a copilot-only option; warn when used with --tool claude."""
        settings_path = tmp_path / ".claude" / "settings.json"
        runner = CliRunner()
        with patch("crossmem.commands.hooks._claude_settings_path", return_value=settings_path):
            result = runner.invoke(main, ["install-hook", "--tool", "claude", "--if-stale"])
        assert result.exit_code == 0
        assert "Warning" in result.output


class TestRecallFormatCopilot:
    """Tests for recall --format copilot."""

    FAKE_RECALL = "# crossmem: myproject\n- use fastapi\n"

    def test_format_copilot_wraps_in_markers(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks._get_recall_content", return_value=self.FAKE_RECALL),
        ):
            result = runner.invoke(main, ["recall", "--format", "copilot"])

        assert result.exit_code == 0
        assert COPILOT_CONTENT_MARKER_START in result.output
        assert COPILOT_CONTENT_MARKER_END in result.output

    def test_format_text_default_no_markers(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks._get_recall_content", return_value=self.FAKE_RECALL),
        ):
            result = runner.invoke(main, ["recall"])

        assert result.exit_code == 0
        assert COPILOT_CONTENT_MARKER_START not in result.output
        assert COPILOT_CONTENT_MARKER_END not in result.output


class TestParseBlockTimestamp:
    """Tests for _parse_block_timestamp()."""

    def test_parses_iso_timestamp(self) -> None:
        import datetime

        content = f"{COPILOT_CONTENT_MARKER_START} 2026-04-12T14:32:07 — regenerate: ...\ncontent\n{COPILOT_CONTENT_MARKER_END}"
        ts = _parse_block_timestamp(content)
        assert ts == datetime.datetime(2026, 4, 12, 14, 32, 7)

    def test_returns_none_for_date_only_header(self) -> None:
        """Old-format date-only block returns None — treated as stale."""
        content = f"{COPILOT_CONTENT_MARKER_START} 2026-04-12 — regenerate: ...\ncontent\n{COPILOT_CONTENT_MARKER_END}"
        assert _parse_block_timestamp(content) is None

    def test_returns_none_when_no_block(self) -> None:
        assert _parse_block_timestamp("# No markers here") is None

    def test_build_block_now_contains_time_component(self) -> None:
        """_build_copilot_block should now embed a full ISO timestamp."""
        block = _build_copilot_block("content")
        # Should match YYYY-MM-DDTHH:MM:SS — has a T separator
        assert "T" in block.split("\n")[0]


class TestIfStale:
    """Tests for --if-stale / --max-age staleness check."""

    FAKE_RECALL = "# crossmem: myproject\n- pattern\n"

    def _block_with_age(self, minutes_ago: float) -> str:
        import datetime

        ts = (datetime.datetime.now() - datetime.timedelta(minutes=minutes_ago)).isoformat(
            timespec="seconds"
        )
        return (
            f"{COPILOT_CONTENT_MARKER_START} {ts} — regenerate: crossmem install-hook --tool copilot -->\n"
            "old content\n"
            f"{COPILOT_CONTENT_MARKER_END}\n"
        )

    def test_fresh_block_skips_silently(self, tmp_path: Path) -> None:
        """Block injected 5 min ago with --max-age 30 should produce no output."""
        copilot_path = tmp_path / ".github" / "copilot-instructions.md"
        copilot_path.parent.mkdir(parents=True)
        copilot_path.write_text(self._block_with_age(5))

        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks._get_recall_content", return_value=self.FAKE_RECALL),
        ):
            result = runner.invoke(
                main, ["install-hook", "--tool", "copilot", "--if-stale", "--max-age", "30"]
            )

        assert result.exit_code == 0
        assert result.output.strip() == ""
        # File not changed — still has old content
        assert "old content" in copilot_path.read_text()

    def test_stale_block_triggers_refresh(self, tmp_path: Path) -> None:
        """Block injected 60 min ago with --max-age 30 should re-inject."""
        copilot_path = tmp_path / ".github" / "copilot-instructions.md"
        copilot_path.parent.mkdir(parents=True)
        copilot_path.write_text(self._block_with_age(60))

        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks._get_recall_content", return_value=self.FAKE_RECALL),
        ):
            result = runner.invoke(
                main, ["install-hook", "--tool", "copilot", "--if-stale", "--max-age", "30"]
            )

        assert result.exit_code == 0
        assert "Injected" in result.output
        assert "old content" not in copilot_path.read_text()

    def test_missing_block_triggers_inject(self, tmp_path: Path) -> None:
        """No existing block with --if-stale should inject (first run)."""
        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks._get_recall_content", return_value=self.FAKE_RECALL),
        ):
            result = runner.invoke(main, ["install-hook", "--tool", "copilot", "--if-stale"])

        assert result.exit_code == 0
        assert "Injected" in result.output

    def test_old_date_only_block_treated_as_stale(self, tmp_path: Path) -> None:
        """Block with old date-only header (pre-P3) has no parseable timestamp, treated as stale."""
        copilot_path = tmp_path / ".github" / "copilot-instructions.md"
        copilot_path.parent.mkdir(parents=True)
        old_block = (
            f"{COPILOT_CONTENT_MARKER_START} 2026-04-12 — regenerate: ...\n"
            "old content\n"
            f"{COPILOT_CONTENT_MARKER_END}\n"
        )
        copilot_path.write_text(old_block)

        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks._get_recall_content", return_value=self.FAKE_RECALL),
        ):
            result = runner.invoke(main, ["install-hook", "--tool", "copilot", "--if-stale"])

        assert result.exit_code == 0
        assert "Injected" in result.output

    def test_without_if_stale_always_reinjects(self, tmp_path: Path) -> None:
        """Normal (non-stale-check) invocation always re-injects regardless of age."""
        copilot_path = tmp_path / ".github" / "copilot-instructions.md"
        copilot_path.parent.mkdir(parents=True)
        copilot_path.write_text(self._block_with_age(1))

        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks._get_recall_content", return_value=self.FAKE_RECALL),
        ):
            result = runner.invoke(main, ["install-hook", "--tool", "copilot"])

        assert result.exit_code == 0
        # Either injected or "already up to date" (content may be identical), never silent
        assert result.output.strip() != ""


class TestSetup:
    """Tests for the setup command (4-step orchestration)."""

    def test_setup_runs_all_four_steps(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with (
            patch(
                "crossmem.commands.hooks._claude_settings_path",
                return_value=tmp_path / ".claude" / "settings.json",
            ),
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks.Path.home", return_value=tmp_path),
            patch(
                "crossmem.commands.hooks._get_recall_content", return_value="# crossmem\n- pat\n"
            ),
            patch("crossmem.commands.core.MemoryStore"),
        ):
            result = runner.invoke(main, ["setup"])

        assert result.exit_code == 0
        assert "Claude Code hook" in result.output
        assert "Copilot instructions" in result.output
        assert "Gemini instructions" in result.output
        assert "Ingesting" in result.output
        assert "Done" in result.output

    def test_setup_installs_claude_hook(self, tmp_path: Path) -> None:
        settings_path = tmp_path / ".claude" / "settings.json"
        runner = CliRunner()
        with (
            patch("crossmem.commands.hooks._claude_settings_path", return_value=settings_path),
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks.Path.home", return_value=tmp_path),
            patch(
                "crossmem.commands.hooks._get_recall_content", return_value="# crossmem\n- pat\n"
            ),
            patch("crossmem.commands.core.MemoryStore"),
        ):
            runner.invoke(main, ["setup"])

        assert settings_path.exists()

    def test_setup_injects_copilot_block(self, tmp_path: Path) -> None:
        copilot_path = tmp_path / ".github" / "copilot-instructions.md"
        runner = CliRunner()
        with (
            patch(
                "crossmem.commands.hooks._claude_settings_path",
                return_value=tmp_path / ".claude" / "settings.json",
            ),
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks.Path.home", return_value=tmp_path),
            patch(
                "crossmem.commands.hooks._get_recall_content", return_value="# crossmem\n- pat\n"
            ),
            patch("crossmem.commands.core.MemoryStore"),
        ):
            runner.invoke(main, ["setup"])

        assert copilot_path.exists()
        assert COPILOT_CONTENT_MARKER_START in copilot_path.read_text()

    def test_setup_writes_gemini_instructions(self, tmp_path: Path) -> None:
        gemini_path = tmp_path / ".gemini" / "GEMINI.md"
        runner = CliRunner()
        with (
            patch(
                "crossmem.commands.hooks._claude_settings_path",
                return_value=tmp_path / ".claude" / "settings.json",
            ),
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks.Path.home", return_value=tmp_path),
            patch(
                "crossmem.commands.hooks._get_recall_content", return_value="# crossmem\n- pat\n"
            ),
            patch("crossmem.commands.core.MemoryStore"),
        ):
            runner.invoke(main, ["setup"])

        assert gemini_path.exists()
        assert "mem_recall" in gemini_path.read_text()

    def test_setup_no_memories_copilot_step_graceful(self, tmp_path: Path) -> None:
        """setup should not fail if no memories exist yet (Copilot step skipped)."""
        runner = CliRunner()
        with (
            patch(
                "crossmem.commands.hooks._claude_settings_path",
                return_value=tmp_path / ".claude" / "settings.json",
            ),
            patch("crossmem.commands.hooks.Path.cwd", return_value=tmp_path),
            patch("crossmem.commands.hooks.Path.home", return_value=tmp_path),
            patch("crossmem.commands.hooks._get_recall_content", return_value=None),
            patch("crossmem.commands.core.MemoryStore"),
        ):
            result = runner.invoke(main, ["setup"])

        assert result.exit_code == 0
        assert "Done" in result.output


class TestInit:
    def test_indexes_readme(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "my-api"
        project_dir.mkdir()
        (project_dir / "README.md").write_text(
            "# My API\nA REST API built with FastAPI and PostgreSQL.\n"
        )

        runner = CliRunner()
        with patch(
            "crossmem.commands.core.MemoryStore",
            return_value=MemoryStore(db_path=tmp_path / "test.db"),
        ):
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
        with patch("crossmem.commands.core.MemoryStore", return_value=MemoryStore(db_path=db_path)):
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
        with patch(
            "crossmem.commands.core.MemoryStore",
            return_value=MemoryStore(db_path=tmp_path / "test.db"),
        ):
            result = runner.invoke(main, ["init", "--path", str(empty_dir)])

        assert result.exit_code == 0
        assert "No documentation files found" in result.output

    def test_idempotent(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "# Docs\nComprehensive documentation for the project.\n"
        )

        db_path = tmp_path / "test.db"
        runner = CliRunner()
        with patch("crossmem.commands.core.MemoryStore", return_value=MemoryStore(db_path=db_path)):
            result1 = runner.invoke(main, ["init", "-p", "proj", "--path", str(tmp_path)])
        with patch("crossmem.commands.core.MemoryStore", return_value=MemoryStore(db_path=db_path)):
            result2 = runner.invoke(main, ["init", "-p", "proj", "--path", str(tmp_path)])

        assert "1 new memories" in result1.output
        assert "already up to date" in result2.output

    def test_multiple_docs(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# About\nA Python library for data validation.\n")
        (tmp_path / "CONTRIBUTING.md").write_text(
            "# Contributing\nFork the repo and submit a pull request.\n"
        )

        runner = CliRunner()
        with patch(
            "crossmem.commands.core.MemoryStore",
            return_value=MemoryStore(db_path=tmp_path / "test.db"),
        ):
            result = runner.invoke(main, ["init", "-p", "lib", "--path", str(tmp_path)])

        assert result.exit_code == 0
        assert "2 new memories" in result.output


class TestPromptSearch:
    def test_returns_results_for_matching_prompt(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        store = MemoryStore(db_path=db_path)
        store.add(
            "Always use middleware for credential masking",
            "mcp:mem_save",
            "backend-api",
            "Security",
        )
        store.close()

        hook_input = json.dumps({"prompt": "how should I handle credentials in this service"})

        runner = CliRunner()
        with patch(
            "crossmem.commands.hooks.MemoryStore", return_value=MemoryStore(db_path=db_path)
        ):
            result = runner.invoke(main, ["prompt-search"], input=hook_input)

        assert result.exit_code == 0
        assert "credential" in result.output.lower()

    def test_silent_for_short_prompts(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        store = MemoryStore(db_path=db_path)
        store.add("Some memory content here", "mcp:mem_save", "test", "General")
        store.close()

        hook_input = json.dumps({"prompt": "hi"})

        runner = CliRunner()
        with patch("crossmem.commands.core.MemoryStore", return_value=MemoryStore(db_path=db_path)):
            result = runner.invoke(main, ["prompt-search"], input=hook_input)

        assert result.exit_code == 0
        assert result.output == ""

    def test_silent_for_no_matches(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        store = MemoryStore(db_path=db_path)
        store.add("Python FastAPI patterns", "mcp:mem_save", "backend", "Patterns")
        store.close()

        hook_input = json.dumps({"prompt": "what is the weather like in Tokyo today"})

        runner = CliRunner()
        with patch(
            "crossmem.commands.hooks.MemoryStore", return_value=MemoryStore(db_path=db_path)
        ):
            result = runner.invoke(main, ["prompt-search"], input=hook_input)

        assert result.exit_code == 0
        # No output — no relevant memories
        assert "crossmem" not in result.output

    def test_silent_for_invalid_json(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["prompt-search"], input="not json at all")

        assert result.exit_code == 0
        assert result.output == ""

    def test_silent_for_empty_prompt(self) -> None:
        hook_input = json.dumps({"prompt": ""})

        runner = CliRunner()
        result = runner.invoke(main, ["prompt-search"], input=hook_input)

        assert result.exit_code == 0
        assert result.output == ""

    def test_includes_project_and_section(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        store = MemoryStore(db_path=db_path)
        store.add("JWT tokens rotated on each use", "mcp:mem_save", "backend-api", "Auth")
        store.close()

        hook_input = json.dumps({"prompt": "how do we handle JWT token rotation in our auth"})

        runner = CliRunner()
        with patch(
            "crossmem.commands.hooks.MemoryStore", return_value=MemoryStore(db_path=db_path)
        ):
            result = runner.invoke(main, ["prompt-search"], input=hook_input)

        assert result.exit_code == 0
        assert "(backend-api)" in result.output
        assert "[Auth]" in result.output

    def test_handles_question_mark_in_prompt(self, tmp_path: Path) -> None:
        """Regression: ? is an FTS5 syntax char — must not crash."""
        db_path = tmp_path / "test.db"
        store = MemoryStore(db_path=db_path)
        store.add("Always rotate credentials quarterly", "mcp:mem_save", "proj", "Security")
        store.close()

        hook_input = json.dumps({"prompt": "how do we rotate credentials?"})

        runner = CliRunner()
        with patch(
            "crossmem.commands.hooks.MemoryStore", return_value=MemoryStore(db_path=db_path)
        ):
            result = runner.invoke(main, ["prompt-search"], input=hook_input)

        assert result.exit_code == 0
        assert "credential" in result.output.lower()

    def test_handles_fts5_special_chars(self, tmp_path: Path) -> None:
        """Regression: colons, parens, asterisks etc. are FTS5 syntax — must not crash."""
        db_path = tmp_path / "test.db"
        store = MemoryStore(db_path=db_path)
        store.add("Use pytest fixtures for test setup", "mcp:mem_save", "proj", "Testing")
        store.close()

        hook_input = json.dumps({"prompt": "how to use pytest::fixture with (scope=session)?"})

        runner = CliRunner()
        with patch(
            "crossmem.commands.hooks.MemoryStore", return_value=MemoryStore(db_path=db_path)
        ):
            result = runner.invoke(main, ["prompt-search"], input=hook_input)

        # Key assertion: no crash from FTS5 special characters
        assert result.exit_code == 0

    def test_all_stop_words_returns_silent(self, tmp_path: Path) -> None:
        """Regression: prompts with only stop words should not hit the DB."""
        db_path = tmp_path / "test.db"
        store = MemoryStore(db_path=db_path)
        store.add("Some memory", "mcp:mem_save", "proj", "General")
        store.close()

        hook_input = json.dumps({"prompt": "do it now"})

        runner = CliRunner()
        with patch(
            "crossmem.commands.hooks.MemoryStore", return_value=MemoryStore(db_path=db_path)
        ):
            result = runner.invoke(main, ["prompt-search"], input=hook_input)

        assert result.exit_code == 0
        assert result.output == ""

    def test_rank_filter_keeps_strong_matches(self) -> None:
        """Strong BM25 rank (<= -5.0) should be kept."""
        mem = Memory(
            id=1,
            content="credential masking",
            source_file="f.md",
            project="proj",
            section="Sec",
            content_hash="h",
            created_at="t",
        )
        mock_store = type(
            "MockStore",
            (),
            {
                "search": lambda self, *a, **kw: [
                    SearchResult(memory=mem, rank=-10.0, highlight="")
                ],
                "close": lambda self: None,
            },
        )()

        hook_input = json.dumps({"prompt": "handle credentials securely"})
        runner = CliRunner()
        with patch("crossmem.commands.hooks.MemoryStore", return_value=mock_store):
            result = runner.invoke(main, ["prompt-search"], input=hook_input)

        assert result.exit_code == 0
        assert "credential" in result.output.lower()

    def test_rank_filter_removes_weak_matches(self) -> None:
        """Weak BM25 rank (> -5.0) should be filtered when DB has meaningful ranks."""
        mem = Memory(
            id=1,
            content="some noise",
            source_file="f.md",
            project="proj",
            section="Sec",
            content_hash="h",
            created_at="t",
        )
        mock_store = type(
            "MockStore",
            (),
            {
                "search": lambda self, *a, **kw: [
                    SearchResult(memory=mem, rank=-2.0, highlight="")
                ],
                "close": lambda self: None,
            },
        )()

        hook_input = json.dumps({"prompt": "handle credentials securely"})
        runner = CliRunner()
        with patch("crossmem.commands.hooks.MemoryStore", return_value=mock_store):
            result = runner.invoke(main, ["prompt-search"], input=hook_input)

        assert result.exit_code == 0
        assert result.output == ""

    def test_rank_filter_bypassed_for_tiny_db(self) -> None:
        """When best rank >= -1.0 (tiny DB), skip filtering — show all results."""
        mem = Memory(
            id=1,
            content="credential masking",
            source_file="f.md",
            project="proj",
            section="Sec",
            content_hash="h",
            created_at="t",
        )
        mock_store = type(
            "MockStore",
            (),
            {
                "search": lambda self, *a, **kw: [
                    SearchResult(memory=mem, rank=-0.5, highlight="")
                ],
                "close": lambda self: None,
            },
        )()

        hook_input = json.dumps({"prompt": "handle credentials securely"})
        runner = CliRunner()
        with patch("crossmem.commands.hooks.MemoryStore", return_value=mock_store):
            result = runner.invoke(main, ["prompt-search"], input=hook_input)

        assert result.exit_code == 0
        assert "credential" in result.output.lower()


class TestPromptSearchVscodeFormat:
    def test_outputs_json_when_hookEventName_present(self) -> None:
        """VS Code hooks pass hookEventName — output should be JSON with additionalContext."""
        mem = Memory(
            id=1,
            content="credential masking",
            source_file="f.md",
            project="proj",
            section="Sec",
            content_hash="h",
            created_at="t",
        )
        mock_store = type(
            "MockStore",
            (),
            {
                "search": lambda self, *a, **kw: [
                    SearchResult(memory=mem, rank=-10.0, highlight="")
                ],
                "close": lambda self: None,
            },
        )()

        hook_input = json.dumps(
            {
                "prompt": "handle credentials securely",
                "hookEventName": "UserPromptSubmit",
                "sessionId": "test",
            }
        )
        runner = CliRunner()
        with patch("crossmem.commands.hooks.MemoryStore", return_value=mock_store):
            result = runner.invoke(main, ["prompt-search"], input=hook_input)

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "credential" in parsed["hookSpecificOutput"]["additionalContext"].lower()

    def test_outputs_plain_text_without_hookEventName(self) -> None:
        """Claude Code format (no hookEventName) — output should be plain text."""
        mem = Memory(
            id=1,
            content="credential masking",
            source_file="f.md",
            project="proj",
            section="Sec",
            content_hash="h",
            created_at="t",
        )
        mock_store = type(
            "MockStore",
            (),
            {
                "search": lambda self, *a, **kw: [
                    SearchResult(memory=mem, rank=-10.0, highlight="")
                ],
                "close": lambda self: None,
            },
        )()

        hook_input = json.dumps({"prompt": "handle credentials securely"})
        runner = CliRunner()
        with patch("crossmem.commands.hooks.MemoryStore", return_value=mock_store):
            result = runner.invoke(main, ["prompt-search"], input=hook_input)

        assert result.exit_code == 0
        assert result.output.startswith("# crossmem:")
        # Should NOT be JSON
        assert "hookSpecificOutput" not in result.output


class TestRecallVscodeFormat:
    def test_recall_format_vscode(self, tmp_path: Path) -> None:
        """recall --format vscode should output SessionStart JSON."""
        db_path = tmp_path / "test.db"
        store = MemoryStore(db_path=db_path)
        store.add("Always rotate credentials", "mcp:mem_save", "test-proj", "Security")
        store.close()

        runner = CliRunner()
        with patch(
            "crossmem.commands.hooks.MemoryStore", return_value=MemoryStore(db_path=db_path)
        ):
            result = runner.invoke(main, ["recall", "-p", "test-proj", "--format", "vscode"])

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert "credential" in parsed["hookSpecificOutput"]["additionalContext"].lower()


class TestInstallHookCopilotAgent:
    def test_dry_run(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch(
            "crossmem.commands.hooks._find_crossmem_bin", return_value="/usr/local/bin/crossmem"
        ):
            result = runner.invoke(main, ["install-hook", "--tool", "copilot-agent", "--dry-run"])

        assert result.exit_code == 0
        assert "Would create" in result.output
        assert "SessionStart" in result.output
        assert "UserPromptSubmit" in result.output
        assert not (tmp_path / ".github" / "hooks" / "crossmem.json").exists()

    def test_install_creates_file(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch(
            "crossmem.commands.hooks._find_crossmem_bin", return_value="/usr/local/bin/crossmem"
        ):
            result = runner.invoke(main, ["install-hook", "--tool", "copilot-agent"])

        assert result.exit_code == 0
        hooks_path = tmp_path / ".github" / "hooks" / "crossmem.json"
        assert hooks_path.exists()
        config = json.loads(hooks_path.read_text())
        assert "SessionStart" in config["hooks"]
        assert "UserPromptSubmit" in config["hooks"]
        assert "--format vscode" in config["hooks"]["SessionStart"][0]["command"]

    def test_uninstall_removes_file(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        hooks_path = tmp_path / ".github" / "hooks" / "crossmem.json"
        hooks_path.parent.mkdir(parents=True)
        hooks_path.write_text("{}")

        runner = CliRunner()
        result = runner.invoke(main, ["install-hook", "--tool", "copilot-agent", "--uninstall"])

        assert result.exit_code == 0
        assert not hooks_path.exists()

    def test_uninstall_noop_when_missing(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["install-hook", "--tool", "copilot-agent", "--uninstall"])

        assert result.exit_code == 0
        assert "No crossmem hook found" in result.output
