"""Tests for ingest module."""

from pathlib import Path

from crossmem.ingest import (
    extract_gemini_project,
    extract_project_name,
    ingest_claude_memory,
    ingest_gemini_memory,
    parse_markdown_sections,
)
from crossmem.store import MemoryStore


class TestExtractProjectName:
    def test_standard_claude_path(self) -> None:
        path = Path.home() / ".claude/projects/-Users-foo-Documents-myproject/memory/MEMORY.md"
        assert extract_project_name(path) == "Documents-myproject"

    def test_deep_path_takes_last_two(self) -> None:
        path = Path.home() / ".claude/projects/-Users-foo-work-backend-api/memory/MEMORY.md"
        assert extract_project_name(path) == "backend-api"

    def test_single_segment(self) -> None:
        path = Path.home() / ".claude/projects/-myproject/memory/MEMORY.md"
        assert extract_project_name(path) == "myproject"

    def test_fallback_to_parent(self) -> None:
        path = Path("/some/random/path/notes.md")
        assert extract_project_name(path) == "path"

    def test_subdirectory_memory_file(self) -> None:
        path = Path.home() / ".claude/projects/-Users-foo-proj/memory/patterns.md"
        assert extract_project_name(path) == "foo-proj"


class TestParseMarkdownSections:
    def test_splits_by_heading(self) -> None:
        content = (
            "# Section A\nThis is long enough content for section A to pass.\n\n"
            "# Section B\nThis is long enough content for section B to pass."
        )
        sections = parse_markdown_sections(content)
        assert len(sections) == 2
        assert sections[0][0] == "Section A"
        assert sections[1][0] == "Section B"

    def test_skips_short_sections(self) -> None:
        content = (
            "# Header\nToo short.\n\n"
            "# Real Section\nThis section has enough content to pass the length filter."
        )
        sections = parse_markdown_sections(content)
        assert len(sections) == 1
        assert sections[0][0] == "Real Section"

    def test_no_headings_returns_empty(self) -> None:
        content = "Just plain text without headings, long enough to matter in a scenario."
        sections = parse_markdown_sections(content)
        assert len(sections) == 1
        assert sections[0][0] == ""

    def test_empty_content(self) -> None:
        sections = parse_markdown_sections("")
        assert sections == []


class TestIngestClaudeMemory:
    def test_ingests_from_fixture(self, tmp_path: Path) -> None:
        # Create a mock Claude project structure
        proj_dir = tmp_path / ".claude/projects/-Users-test-myproject/memory"
        proj_dir.mkdir(parents=True)
        (proj_dir / "MEMORY.md").write_text(
            "# Architecture\nFastAPI backend with PostgreSQL for persistence.\n\n"
            "# Testing\nAll tests use pytest with mocked external services.\n"
        )

        store = MemoryStore(db_path=tmp_path / "test.db")
        added = ingest_claude_memory(store, base_path=tmp_path / ".claude/projects")
        assert added == 2
        assert store.count() == 2

    def test_no_projects_dir(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        added = ingest_claude_memory(store, base_path=tmp_path / "nonexistent")
        assert added == 0

    def test_idempotent(self, tmp_path: Path) -> None:
        proj_dir = tmp_path / ".claude/projects/-Users-test-proj/memory"
        proj_dir.mkdir(parents=True)
        (proj_dir / "MEMORY.md").write_text(
            "# Notes\nMeaningful content long enough to pass the section filter.\n"
        )

        store = MemoryStore(db_path=tmp_path / "test.db")
        first = ingest_claude_memory(store, base_path=tmp_path / ".claude/projects")
        second = ingest_claude_memory(store, base_path=tmp_path / ".claude/projects")
        assert first == 1
        assert second == 0


class TestExtractGeminiProject:
    def test_quoted_project(self) -> None:
        text = "For the 'rag-accuracy-fine-tune' project, I must follow..."
        assert extract_gemini_project(text) == "rag-accuracy-fine-tune"

    def test_unquoted_project(self) -> None:
        text = "For the myproject project, always use Python 3.12."
        assert extract_gemini_project(text) == "myproject"

    def test_no_project_reference(self) -> None:
        text = "Always use snake_case for variable names."
        assert extract_gemini_project(text) == "gemini"


class TestIngestGeminiMemory:
    def test_ingests_bullets(self, tmp_path: Path) -> None:
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "GEMINI.md").write_text(
            "## Gemini Added Memories\n"
            "- For the 'myproject' project, use FastAPI with async handlers.\n"
            "- The RAG system UI has been refactored into a three-tab cockpit.\n"
        )

        store = MemoryStore(db_path=tmp_path / "test.db")
        added = ingest_gemini_memory(store, base_path=gemini_dir)
        assert added == 2
        assert store.count() == 2

    def test_extracts_project_from_bullet(self, tmp_path: Path) -> None:
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "GEMINI.md").write_text(
            "## Gemini Added Memories\n"
            "- For the 'alpha' project, always run tests before commit.\n"
        )

        store = MemoryStore(db_path=tmp_path / "test.db")
        ingest_gemini_memory(store, base_path=gemini_dir)
        results = store.search("tests")
        assert results[0].memory.project == "alpha"

    def test_skips_short_bullets(self, tmp_path: Path) -> None:
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "GEMINI.md").write_text(
            "## Gemini Added Memories\n"
            "- Short.\n"
            "- This bullet is long enough to be a meaningful memory entry.\n"
        )

        store = MemoryStore(db_path=tmp_path / "test.db")
        added = ingest_gemini_memory(store, base_path=gemini_dir)
        assert added == 1

    def test_no_gemini_file(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        added = ingest_gemini_memory(store, base_path=tmp_path / "nonexistent")
        assert added == 0

    def test_idempotent(self, tmp_path: Path) -> None:
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "GEMINI.md").write_text(
            "## Gemini Added Memories\n"
            "- For the 'proj' project, use structured logging everywhere.\n"
        )

        store = MemoryStore(db_path=tmp_path / "test.db")
        first = ingest_gemini_memory(store, base_path=gemini_dir)
        second = ingest_gemini_memory(store, base_path=gemini_dir)
        assert first == 1
        assert second == 0
