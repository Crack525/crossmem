"""Tests for Claude → Gemini sync."""

from pathlib import Path

from crossmem.store import MemoryStore
from crossmem.sync import (
    SYNC_END,
    SYNC_START,
    claude_to_gemini_bullet,
    collect_claude_memories,
    filter_for_project,
    sync_once,
    write_to_gemini,
)


class TestClaudeToGeminiBullet:
    def test_with_section(self) -> None:
        result = claude_to_gemini_bullet(
            "myproject", "Security", "Credentials are masked before persisting."
        )
        assert result.startswith("- For the 'myproject' project (Security):")
        assert "Credentials are masked" in result

    def test_without_section(self) -> None:
        result = claude_to_gemini_bullet("myproject", "", "Some general note.")
        assert result == "- For the 'myproject' project: Some general note."

    def test_multiline_collapsed(self) -> None:
        content = "Line one.\nLine two.\nLine three."
        result = claude_to_gemini_bullet("proj", "Notes", content)
        assert "\n" not in result
        assert "Line one. Line two. Line three." in result

    def test_long_content_truncated(self) -> None:
        content = "x" * 600
        result = claude_to_gemini_bullet("proj", "", content)
        # "- For the 'proj' project: " prefix + 500 char max content
        assert result.endswith("...")
        assert len(result) < 600


class TestCollectClaudeMemories:
    def test_collects_from_fixture(self, tmp_path: Path) -> None:
        proj_dir = tmp_path / "-Users-test-myproject/memory"
        proj_dir.mkdir(parents=True)
        (proj_dir / "MEMORY.md").write_text(
            "# Arch\nFastAPI backend with PostgreSQL for data.\n\n"
            "# Tests\nAll tests use pytest with mocked externals.\n"
        )
        memories = collect_claude_memories(tmp_path)
        assert len(memories) == 2
        assert memories[0][0] == "test-myproject"  # project
        assert memories[0][1] == "Arch"  # section

    def test_empty_dir(self, tmp_path: Path) -> None:
        assert collect_claude_memories(tmp_path / "nonexistent") == []


class TestWriteToGemini:
    def test_creates_new_file(self, tmp_path: Path) -> None:
        gemini_path = tmp_path / "GEMINI.md"
        block = f"{SYNC_START}\n- bullet one\n{SYNC_END}"
        count, changed = write_to_gemini(block, gemini_path)
        assert changed is True
        content = gemini_path.read_text()
        assert SYNC_START in content
        assert "bullet one" in content

    def test_preserves_existing_content(self, tmp_path: Path) -> None:
        gemini_path = tmp_path / "GEMINI.md"
        gemini_path.write_text(
            "## Gemini Added Memories\n- My own memory.\n"
        )
        block = f"{SYNC_START}\n- synced bullet\n{SYNC_END}"
        write_to_gemini(block, gemini_path)
        content = gemini_path.read_text()
        assert "My own memory" in content
        assert "synced bullet" in content

    def test_replaces_existing_sync_block(self, tmp_path: Path) -> None:
        gemini_path = tmp_path / "GEMINI.md"
        gemini_path.write_text(
            f"## Own\n- mine\n\n{SYNC_START}\n- old\n{SYNC_END}\n"
        )
        block = f"{SYNC_START}\n- new\n{SYNC_END}"
        write_to_gemini(block, gemini_path)
        content = gemini_path.read_text()
        assert "- old" not in content
        assert "- new" in content
        assert "- mine" in content

    def test_no_change_returns_false(self, tmp_path: Path) -> None:
        gemini_path = tmp_path / "GEMINI.md"
        block = f"{SYNC_START}\n- bullet\n{SYNC_END}"
        write_to_gemini(block, gemini_path)
        _, changed = write_to_gemini(block, gemini_path)
        assert changed is False


class TestFilterForProject:
    def test_includes_target_project(self) -> None:
        memories = [
            ("alpha", "Config", "use python 3.12"),
            ("beta", "Config", "use node 20"),
        ]
        result = filter_for_project(memories, "alpha")
        assert len(result) == 2  # alpha's + shared "Config" from beta
        projects = {m[0] for m in result}
        assert "alpha" in projects

    def test_includes_shared_sections(self) -> None:
        memories = [
            ("alpha", "Security", "mask credentials"),
            ("beta", "Security", "mask api keys"),
            ("gamma", "Deployment", "use docker"),
        ]
        result = filter_for_project(memories, "alpha")
        # alpha's Security + beta's Security (shared) — not gamma's Deployment
        assert len(result) == 2
        assert all(m[1] == "Security" for m in result)

    def test_excludes_unrelated(self) -> None:
        memories = [
            ("alpha", "Config", "use python"),
            ("beta", "Deployment", "use docker"),
            ("gamma", "Testing", "use pytest"),
        ]
        result = filter_for_project(memories, "alpha")
        # Only alpha's Config — no shared sections
        assert len(result) == 1
        assert result[0][0] == "alpha"

    def test_empty_section_not_shared(self) -> None:
        memories = [
            ("alpha", "", "root content alpha"),
            ("beta", "", "root content beta"),
        ]
        result = filter_for_project(memories, "alpha")
        # Empty sections are not treated as shared
        assert len(result) == 1
        assert result[0][0] == "alpha"


class TestSyncOnce:
    def test_end_to_end(self, tmp_path: Path) -> None:
        # Create Claude memories
        proj_dir = tmp_path / "claude/-Users-test-proj/memory"
        proj_dir.mkdir(parents=True)
        (proj_dir / "MEMORY.md").write_text(
            "# Config\nAlways use temperature 0.0 for deterministic output.\n"
        )

        # Create existing Gemini file
        gemini_path = tmp_path / "GEMINI.md"
        gemini_path.write_text(
            "## Gemini Added Memories\n- Gemini's own memory here.\n"
        )

        count, changed = sync_once(
            claude_path=tmp_path / "claude",
            gemini_path=gemini_path,
        )
        assert changed is True
        assert count >= 1

        content = gemini_path.read_text()
        # Gemini's own memories preserved
        assert "Gemini's own memory here" in content
        # Claude's memories synced
        assert "temperature 0.0" in content
        assert SYNC_START in content
        assert SYNC_END in content

    def test_includes_mem_save_memories(self, tmp_path: Path) -> None:
        # No Claude files — only DB-saved memories
        claude_path = tmp_path / "claude"
        claude_path.mkdir()
        gemini_path = tmp_path / "GEMINI.md"

        store = MemoryStore(db_path=tmp_path / "test.db")
        store.add("Use retry with exponential backoff", "mcp:mem_save", "my-api", "Patterns")

        count, changed = sync_once(
            claude_path=claude_path,
            gemini_path=gemini_path,
            store=store,
        )
        assert changed is True
        content = gemini_path.read_text()
        assert "retry with exponential backoff" in content
        assert "my-api" in content

    def test_mem_save_deduped_with_file_memories(self, tmp_path: Path) -> None:
        # Same content in both file and DB — should appear only once
        proj_dir = tmp_path / "claude/-Users-test-proj/memory"
        proj_dir.mkdir(parents=True)
        (proj_dir / "MEMORY.md").write_text("# Config\nUse temperature 0.0 always.\n")

        store = MemoryStore(db_path=tmp_path / "test.db")
        store.add("Use temperature 0.0 always.", "mcp:mem_save", "test-proj", "Config")

        gemini_path = tmp_path / "GEMINI.md"
        count, changed = sync_once(
            claude_path=tmp_path / "claude",
            gemini_path=gemini_path,
            store=store,
        )
        assert changed is True
        content = gemini_path.read_text()
        # Should appear exactly once
        assert content.count("temperature 0.0") == 1

    def test_with_project_filter(self, tmp_path: Path) -> None:
        # Create two projects with a shared section
        for name in ["alpha", "beta"]:
            proj_dir = tmp_path / f"claude/-Users-test-{name}/memory"
            proj_dir.mkdir(parents=True)
            (proj_dir / "MEMORY.md").write_text(
                f"# Security\nMask credentials in {name} project.\n\n"
                f"# Unique-{name}\nOnly in {name} project notes here.\n"
            )

        gemini_path = tmp_path / "GEMINI.md"
        count, changed = sync_once(
            claude_path=tmp_path / "claude",
            gemini_path=gemini_path,
            project="test-alpha",
        )
        assert changed is True

        content = gemini_path.read_text()
        # Alpha's unique section included
        assert "Unique-alpha" in content
        # Shared "Security" from both projects included
        assert "alpha" in content
        assert "Security" in content
        # Beta's unique section NOT included
        assert "Unique-beta" not in content
