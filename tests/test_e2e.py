"""End-to-end tests simulating a real user workflow.

Covers the full lifecycle across ingest → search → recall → save → update → forget
using a synthetic ~/.claude/projects filesystem so no real user data leaks.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from crossmem.ingest import (
    extract_project_name,
    ingest_claude_memory,
    ingest_copilot_memory,
    ingest_gemini_memory,
    ingest_project_docs,
    parse_markdown_sections,
)
from crossmem.server import mem_forget, mem_get, mem_recall, mem_save, mem_update
from crossmem.store import MemoryStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LARGE_SECTION = "\n\n".join(
    f"Paragraph {i}: This is a detailed explanation of item {i} in the architecture "
    f"covering deployment, scaling, and observability concerns for microservice {i}."
    for i in range(1, 12)
)


def _make_claude_dir(base: Path, encoded_name: str) -> Path:
    """Create a synthetic ~/.claude/projects/<encoded>/memory/ directory."""
    memory_dir = base / encoded_name / "memory"
    memory_dir.mkdir(parents=True)
    return memory_dir


def _write_memory_file(memory_dir: Path, name: str, content: str) -> Path:
    f = memory_dir / name
    f.write_text(content, encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# 1. Project name extraction — all path shapes
# ---------------------------------------------------------------------------


class TestExtractProjectNameEdgeCases:
    """Validate extract_project_name against real-world path patterns."""

    def _path(self, encoded: str) -> Path:
        return Path("/home/alice") / ".claude" / "projects" / encoded / "memory" / "MEMORY.md"

    def test_simple_project(self) -> None:
        path = Path("/home/alice/.claude/projects/-home-alice-code-myapp/memory/MEMORY.md")
        assert extract_project_name(path) == "myapp"

    def test_personal_workspace_stripped(self) -> None:
        path = Path(
            "/home/alice/.claude/projects/-home-alice-Documents-PERSONAL-tokenxray/memory/MEMORY.md"
        )
        assert extract_project_name(path) == "tokenxray"

    def test_workspace_dir_stripped(self) -> None:
        path = Path(
            "/home/alice/.claude/projects/-home-alice-workspace-backend-api/memory/MEMORY.md"
        )
        assert extract_project_name(path) == "backend-api"

    def test_two_segment_project_preserved(self) -> None:
        path = Path("/home/alice/.claude/projects/-home-alice-code-backend-api/memory/MEMORY.md")
        assert extract_project_name(path) == "backend-api"

    def test_short_abbreviation_gets_next_segment(self) -> None:
        """A single ≤2-char segment is extended with the next segment for context."""
        path = Path(
            "/home/alice/.claude/projects/-home-alice-Documents-AB-PLATFORM/memory/MEMORY.md"
        )
        name = extract_project_name(path)
        # Should not be just "ab" — must include context
        assert len(name) > 2

    def test_no_memory_dir_falls_back_gracefully(self) -> None:
        path = Path("/some/random/path/myproject/MEMORY.md")
        name = extract_project_name(path)
        assert isinstance(name, str)
        assert len(name) > 0

    def test_encoded_path_all_workspace_dirs(self) -> None:
        """When every remaining segment is a workspace dir, returns last segment."""
        path = Path("/home/alice/.claude/projects/-home-alice-workspace-projects/memory/MEMORY.md")
        name = extract_project_name(path)
        assert isinstance(name, str)
        assert len(name) > 0

    def test_username_with_dot_stripped(self, tmp_path: Path) -> None:
        """Dots in usernames are encoded as hyphens by Claude; prefix strip must handle this."""
        # Simulate: home=/home/first.last, Claude encoded path has 'first-last'
        fake_home = tmp_path / "home" / "first.last"
        fake_home.mkdir(parents=True)
        claude_dir = fake_home / ".claude" / "projects"
        memory_dir = claude_dir / "-home-first-last-code-myproject" / "memory"
        memory_dir.mkdir(parents=True)
        path = memory_dir / "MEMORY.md"
        with patch("crossmem.ingest.Path") as MockPath:
            # Only intercept Path.home(); all other Path calls use real Path
            original_path = Path

            def path_side_effect(*args):
                return original_path(*args)

            MockPath.side_effect = path_side_effect
            MockPath.home.return_value = fake_home
            # Call the real function but with mocked Path.home
            import crossmem.ingest as ingest_mod

            original_home = ingest_mod.Path
            ingest_mod.Path = type(
                "MockedPath",
                (),
                {
                    "home": staticmethod(lambda: fake_home),
                    "__call__": staticmethod(lambda *a, **kw: original_path(*a, **kw)),
                    "parts": property(lambda self: original_path(self).parts),
                },
            )
            try:
                result = extract_project_name(path)
            finally:
                ingest_mod.Path = original_home

        # The username prefix should have been stripped; result should not include
        # username fragments as the project name
        assert result != "first-last"
        assert result != "first"

    def test_returns_lowercase(self) -> None:
        path = Path("/home/alice/.claude/projects/-home-alice-code-MyProject/memory/MEMORY.md")
        name = extract_project_name(path)
        assert name == name.lower()


# ---------------------------------------------------------------------------
# 2. Section chunking idempotency
# ---------------------------------------------------------------------------


class TestSectionChunking:
    def test_large_section_gets_indexed_chunks(self) -> None:
        """A section exceeding _MAX_SECTION_CHARS is split with [1], [2] suffixes."""
        content = f"# Architecture\n\n{_LARGE_SECTION}\n"
        sections = parse_markdown_sections(content)
        assert len(sections) > 1
        headings = [h for h, _ in sections]
        assert headings[0] == "Architecture [1]"
        assert headings[1] == "Architecture [2]"

    def test_single_chunk_has_no_suffix(self) -> None:
        content = "# Setup\n\nJust a short section that fits in one chunk.\n"
        sections = parse_markdown_sections(content)
        assert len(sections) == 1
        assert sections[0][0] == "Setup"

    def test_chunk_headings_are_unique_per_section(self) -> None:
        """All headings must be unique — duplicate headings cause upsert key collisions."""
        content = f"# Big Section\n\n{_LARGE_SECTION}\n"
        sections = parse_markdown_sections(content)
        headings = [h for h, _ in sections]
        assert len(headings) == len(set(headings)), "Duplicate chunk headings found"

    def test_ingest_large_file_is_idempotent(self, tmp_path: Path) -> None:
        """Two consecutive ingests of a file with large sections must add 0 on second run."""
        memory_dir = tmp_path / "projects" / "-home-alice-code-bigproject" / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "notes.md").write_text(
            f"# Architecture\n\n{_LARGE_SECTION}\n\n# Deployment\n\n{_LARGE_SECTION}\n",
            encoding="utf-8",
        )
        store = MemoryStore(db_path=tmp_path / "test.db")
        ingest_claude_memory(store, base_path=tmp_path / "projects")
        n2 = ingest_claude_memory(store, base_path=tmp_path / "projects")
        assert n2 == 0, f"Second ingest added {n2} (expected 0)"


# ---------------------------------------------------------------------------
# 3. Claude memory ingest — full synthetic filesystem
# ---------------------------------------------------------------------------


class TestClaudeMemoryIngest:
    def _build_filesystem(self, tmp_path: Path) -> Path:
        """Build a synthetic ~/.claude/projects filesystem with varied patterns."""
        base = tmp_path / "claude" / "projects"

        # Project A: simple single-file memory
        d = _make_claude_dir(base, "-home-alice-code-alpha-service")
        _write_memory_file(
            d, "feedback.md", "# Deployment\n\nAlways run db migrate before deploy.\n"
        )

        # Project B: MEMORY.md with siblings — MEMORY.md must be skipped
        d = _make_claude_dir(base, "-home-alice-code-beta-app")
        _write_memory_file(d, "MEMORY.md", "- [Gotchas](gotchas.md) — skip\n")
        _write_memory_file(d, "gotchas.md", "# Gotchas\n\nNever mutate shared state in handlers.\n")

        # Project C: MEMORY.md with no siblings — should be ingested
        d = _make_claude_dir(base, "-home-alice-code-gamma-lib")
        _write_memory_file(d, "MEMORY.md", "# Notes\n\nUse abstract base classes for plugins.\n")

        # Project D: large section that must be chunked
        d = _make_claude_dir(base, "-home-alice-code-delta-platform")
        _write_memory_file(
            d,
            "arch.md",
            f"# Architecture\n\n{_LARGE_SECTION}\n\n# Deployment\n\nDeploy via CI only.\n",
        )

        # Project E: workspace dir in path
        d = _make_claude_dir(base, "-home-alice-workspace-epsilon-svc")
        _write_memory_file(d, "notes.md", "# Auth\n\nUse PKCE flow for OAuth.\n")

        # Project F: empty file — must be skipped
        d = _make_claude_dir(base, "-home-alice-code-zeta-tool")
        _write_memory_file(d, "empty.md", "   \n  ")

        return base

    def test_ingests_all_projects(self, tmp_path: Path) -> None:
        base = self._build_filesystem(tmp_path)
        store = MemoryStore(db_path=tmp_path / "test.db")
        added = ingest_claude_memory(store, base_path=base)
        assert added > 0
        projects = store.list_projects()
        assert "alpha-service" in projects
        assert "beta-app" in projects
        assert "gamma-lib" in projects
        assert "delta-platform" in projects
        assert "epsilon-svc" in projects

    def test_memory_md_with_siblings_is_skipped(self, tmp_path: Path) -> None:
        base = self._build_filesystem(tmp_path)
        store = MemoryStore(db_path=tmp_path / "test.db")
        ingest_claude_memory(store, base_path=base)
        beta_mems = store.get_by_project("beta-app")
        contents = [m.content for m in beta_mems]
        # Pointer line from MEMORY.md must not appear
        assert not any("skip" in c for c in contents)
        # Actual gotchas content must be present
        assert any("mutate" in c for c in contents)

    def test_memory_md_without_siblings_is_ingested(self, tmp_path: Path) -> None:
        base = self._build_filesystem(tmp_path)
        store = MemoryStore(db_path=tmp_path / "test.db")
        ingest_claude_memory(store, base_path=base)
        gamma_mems = store.get_by_project("gamma-lib")
        assert any("abstract base" in m.content for m in gamma_mems)

    def test_empty_file_is_skipped(self, tmp_path: Path) -> None:
        base = self._build_filesystem(tmp_path)
        store = MemoryStore(db_path=tmp_path / "test.db")
        ingest_claude_memory(store, base_path=base)
        assert "zeta-tool" not in store.list_projects()

    def test_idempotency_zero_duplicates(self, tmp_path: Path) -> None:
        base = self._build_filesystem(tmp_path)
        store = MemoryStore(db_path=tmp_path / "test.db")
        ingest_claude_memory(store, base_path=base)
        n2 = ingest_claude_memory(store, base_path=base)
        assert n2 == 0, f"Re-ingest added {n2} items (expected 0)"

    def test_content_update_on_file_change(self, tmp_path: Path) -> None:
        base = self._build_filesystem(tmp_path)
        store = MemoryStore(db_path=tmp_path / "test.db")
        ingest_claude_memory(store, base_path=base)

        # Modify the file
        notes_path = base / "-home-alice-code-alpha-service" / "memory" / "feedback.md"
        notes_path.write_text(
            "# Deployment\n\nAlways run db migrate AND seed before deploy.\n",
            encoding="utf-8",
        )
        updated = ingest_claude_memory(store, base_path=base)
        assert updated == 1  # only the changed file re-ingested
        mems = store.get_by_project("alpha-service")
        assert any("seed" in m.content for m in mems)


# ---------------------------------------------------------------------------
# 4. Gemini and Copilot ingest
# ---------------------------------------------------------------------------


class TestGeminiIngest:
    def test_ingests_bullets(self, tmp_path: Path) -> None:
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "GEMINI.md").write_text(
            "- Use async generators for streaming responses in FastAPI.\n"
            "- For the 'my-backend' project: always use connection pooling.\n"
            "- Short.\n",
            encoding="utf-8",
        )
        store = MemoryStore(db_path=tmp_path / "test.db")
        added = ingest_gemini_memory(store, base_path=gemini_dir)
        assert added == 2  # "Short." is < 20 chars, skipped

    def test_project_extracted_from_for_clause(self, tmp_path: Path) -> None:
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "GEMINI.md").write_text(
            "- For the 'api-gateway' project: rate limit all external calls.\n",
            encoding="utf-8",
        )
        store = MemoryStore(db_path=tmp_path / "test.db")
        ingest_gemini_memory(store, base_path=gemini_dir)
        assert "api-gateway" in store.list_projects()

    def test_missing_file_returns_zero(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "test.db")
        assert ingest_gemini_memory(store, base_path=tmp_path / "nonexistent") == 0

    def test_idempotent(self, tmp_path: Path) -> None:
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "GEMINI.md").write_text(
            "- Always pin dependency versions in pyproject.toml.\n",
            encoding="utf-8",
        )
        store = MemoryStore(db_path=tmp_path / "test.db")
        ingest_gemini_memory(store, base_path=gemini_dir)
        n2 = ingest_gemini_memory(store, base_path=gemini_dir)
        assert n2 == 0


class TestCopilotIngest:
    def test_ingests_markdown_files(self, tmp_path: Path) -> None:
        copilot_dir = tmp_path / "memories"
        copilot_dir.mkdir()
        (copilot_dir / "general.md").write_text(
            "# Testing\n\nAlways write tests before merging to main.\n",
            encoding="utf-8",
        )
        store = MemoryStore(db_path=tmp_path / "test.db")
        added = ingest_copilot_memory(store, base_path=copilot_dir)
        assert added == 1

    def test_idempotent(self, tmp_path: Path) -> None:
        copilot_dir = tmp_path / "memories"
        copilot_dir.mkdir()
        (copilot_dir / "g.md").write_text(
            "# Arch\n\nDecouple service boundaries with message queues.\n",
            encoding="utf-8",
        )
        store = MemoryStore(db_path=tmp_path / "test.db")
        ingest_copilot_memory(store, base_path=copilot_dir)
        n2 = ingest_copilot_memory(store, base_path=copilot_dir)
        assert n2 == 0


# ---------------------------------------------------------------------------
# 5. Project docs ingest
# ---------------------------------------------------------------------------


class TestProjectDocsIngest:
    def test_readme_and_claude_md(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "# My Service\n\nA FastAPI microservice for user management.\n",
            encoding="utf-8",
        )
        (tmp_path / "CLAUDE.md").write_text(
            "# Dev Notes\n\nRun `make dev` to start the dev server.\n",
            encoding="utf-8",
        )
        store = MemoryStore(db_path=tmp_path / "test.db")
        added = ingest_project_docs(store, tmp_path, project="my-svc")
        assert added == 2

    def test_strips_crossmem_injected_block(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text(
            "# Real content\n\nActual project notes here.\n\n"
            "<!-- crossmem:auto-injected 2026-01-01T00:00:00 -->\n"
            "# crossmem: my-svc\n- Some injected memory\n"
            "<!-- crossmem:end -->\n",
            encoding="utf-8",
        )
        store = MemoryStore(db_path=tmp_path / "test.db")
        ingest_project_docs(store, tmp_path, project="my-svc")
        mems = store.get_by_project("my-svc")
        assert all("crossmem:auto-injected" not in m.content for m in mems)
        assert any("Actual project notes" in m.content for m in mems)

    def test_idempotent_re_ingest(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "# Stable\n\nThis content does not change between runs.\n",
            encoding="utf-8",
        )
        store = MemoryStore(db_path=tmp_path / "test.db")
        ingest_project_docs(store, tmp_path, project="stable-proj")
        n2 = ingest_project_docs(store, tmp_path, project="stable-proj")
        assert n2 == 0


# ---------------------------------------------------------------------------
# 6. MCP tools — full user session lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture()
def mcp_store(tmp_path: pytest.TempPathFactory) -> MemoryStore:
    store = MemoryStore(db_path=Path(":memory:"))
    store.close = lambda: None  # prevent tools from closing the shared store
    return store


@pytest.fixture()
def patched_store(mcp_store: MemoryStore):
    with patch("crossmem.server.get_store", return_value=mcp_store):
        yield mcp_store


class TestMcpSessionLifecycle:
    """Simulate a real agent session: recall → work → save → update → forget."""

    def test_cold_start_recall_no_project(self, patched_store: MemoryStore) -> None:
        result = mem_recall(project="ghost-project", cwd="/home/alice/ghost-project")
        assert "No memories found" in result

    def test_save_then_recall(self, patched_store: MemoryStore) -> None:
        mem_save(
            content="Deploy with: uv publish --token $TOKEN",
            project="my-proj",
            section="Release",
        )
        result = mem_recall(project="my-proj", cwd="/home/alice/my-proj")
        assert "Deploy with" in result
        assert "Release" in result

    def test_recall_includes_session_footer(self, patched_store: MemoryStore) -> None:
        mem_save(content="Always use structured logging in prod.", project="my-proj")
        result = mem_recall(project="my-proj", cwd="/home/alice/my-proj")
        assert "mem_save" in result  # session footer references mem_save

    def test_save_content_too_long_rejected(self, patched_store: MemoryStore) -> None:
        result = mem_save(content="x" * 1001, project="my-proj")
        assert "too long" in result
        assert patched_store.count() == 0

    def test_save_content_at_limit_accepted(self, patched_store: MemoryStore) -> None:
        result = mem_save(content="x" * 1000, project="my-proj")
        assert "Saved" in result

    def test_save_duplicate_rejected(self, patched_store: MemoryStore) -> None:
        mem_save(content="Never cache auth tokens in localStorage.", project="my-proj")
        result = mem_save(content="Never cache auth tokens in localStorage.", project="my-proj")
        assert "already exists" in result
        assert patched_store.count() == 1

    def test_save_near_duplicate_hints(self, patched_store: MemoryStore) -> None:
        # Old memory is a superset: contains all tokens from the new (shorter) memory.
        # The AND query on the new probe then matches both, surfacing the old one as a hint.
        mem_save(
            content=(
                "Use connection pooling to improve database performance and reduce query "
                "latency under heavy load in production deployments for optimal throughput."
            ),
            project="my-proj",
            section="DB",
        )
        result = mem_save(
            content="Use connection pooling to reduce latency in production.",
            project="my-proj",
            section="DB",
        )
        assert "Saved" in result
        assert "Similar" in result

    def test_search_after_save(self, patched_store: MemoryStore) -> None:
        from crossmem.server import mem_search

        mem_save(content="Use alembic for database migrations.", project="my-proj", section="DB")
        result = mem_search(query="alembic migrations")
        assert "alembic" in result.lower()

    def test_get_full_content(self, patched_store: MemoryStore) -> None:
        long = "Architecture decision: " + ("detail " * 80)
        mem_save(content=long[:1000], project="my-proj", section="Arch")
        mems = patched_store.get_by_project("my-proj")
        result = mem_get(memory_id=mems[0].id)
        assert "Architecture decision" in result

    def test_update_preserves_id(self, patched_store: MemoryStore) -> None:
        mem_save(content="Old deployment note.", project="my-proj")
        mems = patched_store.get_by_project("my-proj")
        original_id = mems[0].id
        mem_update(memory_id=original_id, content="Updated deployment note with rollback steps.")
        mem = patched_store.get(original_id)
        assert mem is not None
        assert "rollback" in mem.content

    def test_update_nonexistent_memory(self, patched_store: MemoryStore) -> None:
        result = mem_update(memory_id=99999, content="This memory does not exist in the store.")
        assert "not found" in result

    def test_forget_removes_memory(self, patched_store: MemoryStore) -> None:
        mem_save(content="Temporary note to be deleted.", project="my-proj")
        mems = patched_store.get_by_project("my-proj")
        mem_id = mems[0].id
        result = mem_forget(memory_id=mem_id)
        assert "Deleted" in result
        assert patched_store.get(mem_id) is None

    def test_forget_nonexistent(self, patched_store: MemoryStore) -> None:
        result = mem_forget(memory_id=99999)
        assert "not found" in result

    def test_recall_with_query_returns_scoped_results(self, patched_store: MemoryStore) -> None:
        mem_save(content="Run migrations with: flask db upgrade", project="flask-app", section="DB")
        mem_save(content="Use gunicorn for production WSGI.", project="flask-app", section="Deploy")
        result = mem_recall(
            project="flask-app", cwd="/home/alice/flask-app", query="migrations database"
        )
        assert "flask" in result.lower() or "migration" in result.lower()

    def test_recall_query_fallback_shows_all(self, patched_store: MemoryStore) -> None:
        mem_save(content="Always lint before commit.", project="flask-app")
        result = mem_recall(
            project="flask-app", cwd="/home/alice/flask-app", query="xyzzy-nonexistent-term"
        )
        assert "flask-app" in result

    def test_global_scope_surfaces_cross_project(self, patched_store: MemoryStore) -> None:
        patched_store.add(
            content="Always pin dependency versions in CI.",
            source_file="mcp:mem_save",
            project="proj-a",
            scope="global",
        )
        result = mem_recall(project="proj-b", cwd="/home/alice/proj-b")
        assert "pin dependency" in result or "Cross-project" in result


# ---------------------------------------------------------------------------
# 7. Scope model — project vs global
# ---------------------------------------------------------------------------


class TestScopeModel:
    def test_save_global_scope(self, patched_store: MemoryStore) -> None:
        result = mem_save(
            content="Always use semantic versioning across all projects.",
            project="any-proj",
            scope="global",
        )
        assert "Saved" in result
        mems = patched_store.get_by_project("any-proj")
        assert mems[0].scope == "global"

    def test_auto_promote_identical_mcp_save(self, patched_store: MemoryStore) -> None:
        identical = "Use feature flags for all major releases."
        patched_store.add(identical, "mcp:mem_save", "proj-a", scope="project")
        patched_store.add(identical, "mcp:mem_save", "proj-b", scope="project")
        promoted = patched_store.auto_promote_patterns(min_projects=2)
        assert promoted == 2
        globals_ = patched_store.get_global_memories()
        assert any(identical in m.content for m in globals_)

    def test_auto_promote_requires_min_projects(self, patched_store: MemoryStore) -> None:
        patched_store.add("Only in one project.", "mcp:mem_save", "proj-a", scope="project")
        promoted = patched_store.auto_promote_patterns(min_projects=2)
        assert promoted == 0

    def test_ingested_docs_not_promoted(self, patched_store: MemoryStore) -> None:
        """Ingested doc duplicates across projects must never be auto-promoted."""
        same_readme = "This project uses MIT license."
        patched_store.add(same_readme, "init:README.md", "proj-a", scope="project")
        patched_store.add(same_readme, "init:README.md", "proj-b", scope="project")
        promoted = patched_store.auto_promote_patterns(min_projects=2)
        assert promoted == 0


# ---------------------------------------------------------------------------
# 8. cwd auto-detection
# ---------------------------------------------------------------------------


class TestCwdAutoDetection:
    def test_save_derives_project_from_unknown_cwd(self, patched_store: MemoryStore) -> None:
        result = mem_save(content="Fresh project note.", cwd="/home/alice/fresh-service")
        assert "fresh-service" in result

    def test_recall_auto_detects_from_cwd(self, patched_store: MemoryStore) -> None:
        patched_store.add("Deploy note", "mcp:mem_save", "known-svc")
        with patch("crossmem.server.os.getcwd", return_value="/home/alice/known-svc"):
            result = mem_recall(cwd="/home/alice/known-svc")
        assert "known-svc" in result or "Deploy note" in result

    def test_recall_unknown_cwd_no_crash(self, patched_store: MemoryStore) -> None:
        result = mem_recall(cwd="/home/alice/totally-unknown-xyz")
        assert "Could not detect" in result or isinstance(result, str)


# ---------------------------------------------------------------------------
# 9. Search edge cases
# ---------------------------------------------------------------------------


class TestSearchEdgeCases:
    def test_empty_store_returns_no_results(self, patched_store: MemoryStore) -> None:
        from crossmem.server import mem_search

        result = mem_search(query="anything")
        assert "No results" in result

    def test_search_with_project_filter(self, patched_store: MemoryStore) -> None:
        from crossmem.server import mem_search

        mem_save(content="Use Redis for session caching.", project="proj-a", section="Infra")
        mem_save(content="Use Redis for rate limiting.", project="proj-b", section="Infra")
        result = mem_search(query="Redis", project="proj-a")
        assert "proj-a" in result
        assert "proj-b" not in result

    def test_search_across_projects(self, patched_store: MemoryStore) -> None:
        from crossmem.server import mem_search

        mem_save(content="Prefer Postgres over MySQL for ACID compliance.", project="svc-a")
        mem_save(content="Postgres JSONB is efficient for semi-structured data.", project="svc-b")
        result = mem_search(query="Postgres")
        assert "svc-a" in result
        assert "svc-b" in result

    def test_search_limit_respected(self, patched_store: MemoryStore) -> None:
        from crossmem.server import mem_search

        for i in range(15):
            patched_store.add(
                f"Memory about caching strategy number {i}.", "mcp:mem_save", f"proj-{i}"
            )
        result = mem_search(query="caching", limit=5)
        # Should not exceed 5 results
        assert result.count("id:") <= 5 or result.count("[") <= 6  # header + 5 results


# ---------------------------------------------------------------------------
# 10. Multi-source combined ingest
# ---------------------------------------------------------------------------


class TestMultiSourceIngest:
    def test_claude_plus_gemini_plus_copilot(self, tmp_path: Path) -> None:
        claude_base = tmp_path / "claude" / "projects"
        gemini_base = tmp_path / ".gemini"
        copilot_base = tmp_path / "copilot"

        # Claude project
        d = _make_claude_dir(claude_base, "-home-alice-code-myapp")
        _write_memory_file(d, "notes.md", "# Arch\n\nService mesh with Istio.\n")

        # Gemini memory
        gemini_base.mkdir(parents=True)
        (gemini_base / "GEMINI.md").write_text(
            "- For the 'myapp' project: use circuit breaker pattern.\n",
            encoding="utf-8",
        )

        # Copilot memory
        copilot_base.mkdir(parents=True)
        (copilot_base / "myapp.md").write_text(
            "# Testing\n\nIntegration tests use testcontainers for real DB.\n",
            encoding="utf-8",
        )

        store = MemoryStore(db_path=tmp_path / "test.db")
        c = ingest_claude_memory(store, base_path=claude_base)
        g = ingest_gemini_memory(store, base_path=gemini_base)
        p = ingest_copilot_memory(store, base_path=copilot_base)
        assert c + g + p >= 3
        assert store.count() >= 3

    def test_all_sources_idempotent(self, tmp_path: Path) -> None:
        claude_base = tmp_path / "claude" / "projects"
        gemini_base = tmp_path / ".gemini"
        copilot_base = tmp_path / "copilot"

        d = _make_claude_dir(claude_base, "-home-alice-code-myapp")
        _write_memory_file(d, "notes.md", "# Arch\n\nService mesh with Istio for mTLS.\n")
        gemini_base.mkdir(parents=True)
        (gemini_base / "GEMINI.md").write_text(
            "- Use structured logging with correlation IDs everywhere.\n",
            encoding="utf-8",
        )
        copilot_base.mkdir(parents=True)
        (copilot_base / "notes.md").write_text(
            "# Deploy\n\nBlue-green deployments cut downtime significantly.\n",
            encoding="utf-8",
        )

        store = MemoryStore(db_path=tmp_path / "test.db")
        ingest_claude_memory(store, base_path=claude_base)
        ingest_gemini_memory(store, base_path=gemini_base)
        ingest_copilot_memory(store, base_path=copilot_base)

        n2c = ingest_claude_memory(store, base_path=claude_base)
        n2g = ingest_gemini_memory(store, base_path=gemini_base)
        n2p = ingest_copilot_memory(store, base_path=copilot_base)
        assert n2c + n2g + n2p == 0, f"Re-ingest added {n2c + n2g + n2p} items"


# ---------------------------------------------------------------------------
# 11. Cross-project patterns in query recall + OR-mode near-duplicate hints
# ---------------------------------------------------------------------------


class TestQueryRecallGlobalPatterns:
    def test_query_recall_includes_global_patterns(self, patched_store: MemoryStore) -> None:
        """Global memories should surface alongside project results when query matches both."""
        patched_store.add(
            content="Always use structured logging with correlation IDs across services.",
            source_file="mcp:mem_save",
            project="shared",
            scope="global",
        )
        mem_save(
            content="Configure logging with loguru for flask-api.",
            project="flask-api",
            section="Logging",
        )
        result = mem_recall(project="flask-api", cwd="/home/alice/flask-api", query="logging")
        assert "Cross-project" in result
        assert "correlation" in result.lower() or "structured" in result.lower()

    def test_query_recall_no_global_duplication(self, patched_store: MemoryStore) -> None:
        """A global memory matching the query should not appear twice."""
        mem_save(
            content="Pin all dependency versions to avoid supply chain drift.",
            project="proj-x",
            scope="global",
        )
        result = mem_recall(project="proj-x", cwd="/home/alice/proj-x", query="dependency")
        assert result.count("Pin all dependency") <= 1


class TestOrModeNearDuplicateHints:
    def test_save_or_mode_hints_different_wording(self, patched_store: MemoryStore) -> None:
        """Hints fire even when old and new memories share only key signal words."""
        mem_save(
            content="Deploy with gunicorn workers behind nginx reverse proxy.",
            project="web-app",
            section="Deploy",
        )
        result = mem_save(
            content="Production gunicorn config: 4 workers, nginx frontend.",
            project="web-app",
            section="Deploy",
        )
        assert "Saved" in result
        assert "Similar" in result

    def test_save_or_mode_no_false_positive_on_common_words(
        self, patched_store: MemoryStore
    ) -> None:
        """Stopword-filtered OR probe should not fire hints for content sharing only stopwords."""
        mem_save(content="Use the standard configuration for all environments.", project="svc")
        result = mem_save(
            content="The default timeout is 30 seconds for all requests.", project="svc"
        )
        assert "Saved" in result


class TestUnknownProjectFallback:
    """Gap 3 — mem_recall for unknown project shows global memories instead of dead-end error."""

    def test_unknown_project_with_globals_shows_cross_project(
        self, patched_store: MemoryStore, tmp_path: Path
    ) -> None:
        patched_store.add(
            "Always pin dependency versions to avoid supply chain drift.",
            "mcp:mem_save",
            "proj-a",
            scope="global",
        )
        result = mem_recall(cwd=str(tmp_path / "brand-new-project"))
        assert "Cross-project patterns" in result
        assert "pin dependency" in result.lower() or "dependency" in result.lower()

    def test_unknown_project_without_globals_returns_error(
        self, patched_store: MemoryStore, tmp_path: Path
    ) -> None:
        result = mem_recall(cwd=str(tmp_path / "totally-unknown"))
        assert "Could not detect project" in result

    def test_unknown_project_with_globals_includes_session_footer(
        self, patched_store: MemoryStore, tmp_path: Path
    ) -> None:
        patched_store.add(
            "Use structured logging in all services.",
            "mcp:mem_save",
            "proj-b",
            scope="global",
        )
        result = mem_recall(cwd=str(tmp_path / "new-service"))
        assert "mem_save" in result
