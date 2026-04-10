"""Tests for ingest module."""

from pathlib import Path

from crossmem.ingest import (
    derive_project_name,
    extract_gemini_project,
    extract_project_name,
    find_project_docs,
    has_project_docs,
    ingest_claude_memory,
    ingest_copilot_memory,
    ingest_gemini_memory,
    ingest_project_docs,
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


class TestIngestCopilotMemory:
    def test_ingests_from_memory_dir(self, tmp_path: Path) -> None:
        mem_dir = tmp_path / "memories"
        mem_dir.mkdir()
        (mem_dir / "project-patterns.md").write_text(
            "# Architecture\nAlways use dependency injection for service layers.\n\n"
            "# Testing\nMock external APIs with httpx fixtures in all tests.\n"
        )

        store = MemoryStore(db_path=tmp_path / "test.db")
        added = ingest_copilot_memory(store, base_path=mem_dir)
        assert added == 2
        assert store.count() == 2

    def test_uses_filename_as_fallback_section(self, tmp_path: Path) -> None:
        mem_dir = tmp_path / "memories"
        mem_dir.mkdir()
        (mem_dir / "docker-tips.md").write_text(
            "Use multi-stage builds to keep images under 200MB.\n"
        )

        store = MemoryStore(db_path=tmp_path / "test.db")
        ingest_copilot_memory(store, base_path=mem_dir)
        memories = store.get_by_project("copilot")
        assert len(memories) == 1
        assert memories[0].section == "Docker Tips"

    def test_project_is_copilot(self, tmp_path: Path) -> None:
        mem_dir = tmp_path / "memories"
        mem_dir.mkdir()
        (mem_dir / "notes.md").write_text(
            "Always check error boundaries in React components.\n"
        )

        store = MemoryStore(db_path=tmp_path / "test.db")
        ingest_copilot_memory(store, base_path=mem_dir)
        memories = store.get_by_project("copilot")
        assert len(memories) == 1

    def test_no_dir_returns_zero(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        added = ingest_copilot_memory(store, base_path=tmp_path / "nonexistent")
        assert added == 0

    def test_idempotent(self, tmp_path: Path) -> None:
        mem_dir = tmp_path / "memories"
        mem_dir.mkdir()
        (mem_dir / "patterns.md").write_text(
            "# Logging\nUse structured logging with correlation IDs everywhere.\n"
        )

        store = MemoryStore(db_path=tmp_path / "test.db")
        first = ingest_copilot_memory(store, base_path=mem_dir)
        second = ingest_copilot_memory(store, base_path=mem_dir)
        assert first == 1
        assert second == 0

    def test_multiple_files(self, tmp_path: Path) -> None:
        mem_dir = tmp_path / "memories"
        mem_dir.mkdir()
        (mem_dir / "security.md").write_text(
            "Never store secrets in environment variables directly.\n"
        )
        (mem_dir / "performance.md").write_text(
            "Use connection pooling for all database connections.\n"
        )

        store = MemoryStore(db_path=tmp_path / "test.db")
        added = ingest_copilot_memory(store, base_path=mem_dir)
        assert added == 2


class TestDeriveProjectName:
    def test_uses_directory_name(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()
        assert derive_project_name(project_dir) == "my-project"

    def test_lowercases_and_hyphenates(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "Backend_API"
        project_dir.mkdir()
        assert derive_project_name(project_dir) == "backend-api"


class TestFindProjectDocs:
    def test_finds_root_docs(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# My Project")
        (tmp_path / "CLAUDE.md").write_text("# Rules")
        docs = find_project_docs(tmp_path)
        names = [d.name for d in docs]
        assert "README.md" in names
        assert "CLAUDE.md" in names

    def test_finds_docs_subdir(self, tmp_path: Path) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "ARCHITECTURE.md").write_text("# Architecture")
        docs = find_project_docs(tmp_path)
        assert len(docs) == 1
        assert docs[0].name == "ARCHITECTURE.md"

    def test_finds_github_copilot_instructions(self, tmp_path: Path) -> None:
        gh_dir = tmp_path / ".github"
        gh_dir.mkdir()
        (gh_dir / "copilot-instructions.md").write_text(
            "# Instructions for copilot"
        )
        docs = find_project_docs(tmp_path)
        assert len(docs) == 1

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        assert find_project_docs(tmp_path) == []

    def test_ignores_non_doc_files(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "random.md").write_text("# Not a doc file")
        assert find_project_docs(tmp_path) == []


class TestHasProjectDocs:
    def test_true_when_readme_exists(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Project")
        assert has_project_docs(tmp_path) is True

    def test_false_when_empty(self, tmp_path: Path) -> None:
        assert has_project_docs(tmp_path) is False


class TestIngestProjectDocs:
    def test_ingests_readme_sections(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "# My Project\nA cool project with lots of features.\n\n"
            "# Architecture\nFastAPI backend with PostgreSQL database.\n"
        )
        store = MemoryStore(db_path=tmp_path / "test.db")
        added = ingest_project_docs(store, tmp_path, project="my-project")
        assert added == 2
        memories = store.get_by_project("my-project")
        sections = {m.section for m in memories}
        assert "My Project" in sections
        assert "Architecture" in sections

    def test_uses_init_source_prefix(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "# Setup\nInstall dependencies with pip install.\n"
        )
        store = MemoryStore(db_path=tmp_path / "test.db")
        ingest_project_docs(store, tmp_path, project="test-proj")
        memories = store.get_by_project("test-proj")
        assert memories[0].source_file.startswith("init:")

    def test_idempotent(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "# Guide\nFollow these steps to get started with the project.\n"
        )
        store = MemoryStore(db_path=tmp_path / "test.db")
        first = ingest_project_docs(store, tmp_path, project="proj")
        second = ingest_project_docs(store, tmp_path, project="proj")
        assert first == 1
        assert second == 0

    def test_no_docs_returns_zero(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        added = ingest_project_docs(store, tmp_path, project="empty")
        assert added == 0

    def test_scans_docs_subdir(self, tmp_path: Path) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "ARCHITECTURE.md").write_text(
            "# Overview\nMicroservices architecture with event-driven communication.\n"
        )
        store = MemoryStore(db_path=tmp_path / "test.db")
        added = ingest_project_docs(store, tmp_path, project="my-svc")
        assert added == 1
        memories = store.get_by_project("my-svc")
        assert memories[0].section == "Overview"

    def test_multiple_doc_files(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "# About\nThis is a Python library for data processing.\n"
        )
        (tmp_path / "CONTRIBUTING.md").write_text(
            "# How to contribute\nFork the repo and submit a pull request.\n"
        )
        store = MemoryStore(db_path=tmp_path / "test.db")
        added = ingest_project_docs(store, tmp_path, project="lib")
        assert added == 2

    def test_updates_on_content_change(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "# Architecture\nFastAPI backend with PostgreSQL database.\n"
        )
        store = MemoryStore(db_path=tmp_path / "test.db")
        first = ingest_project_docs(store, tmp_path, project="proj")
        assert first == 1
        assert store.count() == 1
        old_mem = store.get_by_project("proj")[0]
        old_id = old_mem.id

        # Change content and re-run
        (tmp_path / "README.md").write_text(
            "# Architecture\nFastAPI backend with PostgreSQL and Redis caching.\n"
        )
        second = ingest_project_docs(store, tmp_path, project="proj")
        assert second == 1  # updated, not skipped
        assert store.count() == 1  # no duplicate
        new_mem = store.get_by_project("proj")[0]
        assert new_mem.id == old_id  # same ID preserved
        assert "Redis" in new_mem.content

    def test_no_duplicates_after_multiple_changes(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        for version in range(5):
            (tmp_path / "README.md").write_text(
                f"# Setup\nInstall version {version} of the package.\n"
            )
            ingest_project_docs(store, tmp_path, project="proj")
        assert store.count() == 1
        mem = store.get_by_project("proj")[0]
        assert "version 4" in mem.content

    def test_derives_project_name(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "cool_project"
        project_dir.mkdir()
        (project_dir / "README.md").write_text(
            "# Cool\nA really cool project with many features.\n"
        )
        store = MemoryStore(db_path=tmp_path / "test.db")
        added = ingest_project_docs(store, project_dir)
        assert added == 1
        memories = store.get_by_project("cool-project")
        assert len(memories) == 1
