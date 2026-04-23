"""Tests for MemoryStore."""

from pathlib import Path

import pytest

from crossmem.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(db_path=tmp_path / "test.db")


class TestAdd:
    def test_add_returns_id(self, store: MemoryStore) -> None:
        result = store.add("some content", "file.md", "proj", "section")
        assert result is not None
        assert isinstance(result, int)

    def test_add_duplicate_returns_none(self, store: MemoryStore) -> None:
        store.add("same content", "file.md", "proj")
        result = store.add("same content", "file.md", "proj")
        assert result is None

    def test_same_content_different_project_allowed(self, store: MemoryStore) -> None:
        id1 = store.add("shared content", "f.md", "proj-a")
        id2 = store.add("shared content", "f.md", "proj-b")
        assert id1 is not None
        assert id2 is not None
        assert id1 != id2

    def test_count_increments(self, store: MemoryStore) -> None:
        assert store.count() == 0
        store.add("one", "f.md", "p")
        store.add("two", "f.md", "p")
        assert store.count() == 2


class TestSearch:
    def test_basic_search(self, store: MemoryStore) -> None:
        store.add("Python logging best practices", "f.md", "proj")
        store.add("Java garbage collection tuning", "f.md", "proj")
        results = store.search("logging")
        assert len(results) == 1
        assert "logging" in results[0].memory.content

    def test_hyphenated_term_search(self, store: MemoryStore) -> None:
        store.add("sub-agent isolation improves context quality", "f.md", "proj")
        store.add("cross-tool memory bridge", "f.md", "proj")
        results = store.search("sub-agent")
        assert len(results) == 1
        assert "sub-agent" in results[0].memory.content

    def test_multiple_hyphenated_terms(self, store: MemoryStore) -> None:
        store.add("cross-tool local-first developer infrastructure", "f.md", "proj")
        results = store.search("cross-tool local-first")
        assert len(results) == 1

    def test_and_logic(self, store: MemoryStore) -> None:
        store.add("Python logging best practices", "f.md", "proj")
        store.add("Python garbage collection tuning", "f.md", "proj")
        store.add("Java logging framework setup", "f.md", "proj")
        results = store.search("Python logging")
        assert len(results) == 1
        assert "Python logging" in results[0].memory.content

    def test_project_filter(self, store: MemoryStore) -> None:
        store.add("credential masking in tests", "f.md", "proj-a")
        store.add("credential masking in prod", "f.md", "proj-b")
        results = store.search("credential", project="proj-a")
        assert len(results) == 1
        assert results[0].memory.project == "proj-a"

    def test_limit(self, store: MemoryStore) -> None:
        for i in range(20):
            store.add(f"memory about testing number {i}", "f.md", "proj")
        results = store.search("testing", limit=5)
        assert len(results) == 5

    def test_no_results(self, store: MemoryStore) -> None:
        store.add("something about docker", "f.md", "proj")
        results = store.search("kubernetes")
        assert len(results) == 0

    def test_or_mode_matches_any_term(self, store: MemoryStore) -> None:
        store.add("Always use middleware for credential masking", "f.md", "proj")
        store.add("Docker setup instructions", "f.md", "proj")
        # AND would fail — "handle" and "service" aren't in the memory
        results = store.search("handle credentials service", or_mode=True)
        assert len(results) == 1
        assert "credential" in results[0].memory.content

    def test_highlight_present(self, store: MemoryStore) -> None:
        store.add("credential masking approach", "f.md", "proj")
        results = store.search("credential")
        assert len(results) == 1
        assert ">>>" in results[0].highlight


class TestBuildFtsQuery:
    def test_single_word(self) -> None:
        assert MemoryStore._build_fts_query("docker") == "docker"

    def test_multi_word_and(self) -> None:
        result = MemoryStore._build_fts_query("credential masking")
        assert result == "credential AND masking"

    def test_quoted_phrase(self) -> None:
        result = MemoryStore._build_fts_query('"exact phrase"')
        assert result == '"exact phrase"'

    def test_mixed_quoted_and_words(self) -> None:
        result = MemoryStore._build_fts_query('"exact phrase" other words')
        assert result == '"exact phrase" AND other AND words'

    def test_empty_returns_original(self) -> None:
        assert MemoryStore._build_fts_query("") == ""

    def test_hyphenated_word_quoted(self) -> None:
        result = MemoryStore._build_fts_query("sub-agent isolation")
        assert result == '"sub-agent" AND isolation'

    def test_multiple_hyphenated_words(self) -> None:
        result = MemoryStore._build_fts_query("cross-tool local-first")
        assert result == '"cross-tool" AND "local-first"'

    def test_hyphenated_with_quoted_phrase(self) -> None:
        result = MemoryStore._build_fts_query('"exact phrase" pre-commit hooks')
        assert result == '"exact phrase" AND "pre-commit" AND hooks'

    def test_underscored_word_quoted(self) -> None:
        # FTS5 unicode61 treats "_" as token separator — quote to prevent splitting
        result = MemoryStore._build_fts_query("_e2e_test")
        assert result == '"_e2e_test"'

    def test_leading_underscore_quoted(self) -> None:
        result = MemoryStore._build_fts_query("normal_word other")
        assert result == '"normal_word" AND other'

    def test_underscore_no_false_positive(self, store: MemoryStore) -> None:
        # Searching for "_unique_token" must not match unrelated content containing "unique"
        store.add("unrelated content about unique topics", "f.md", "other", "Sec")
        store.add("target content _unique_token here", "f.md", "target", "Sec")
        results = store.search("_unique_token", limit=10)
        projects = [r.memory.project for r in results]
        assert "target" in projects
        # "other" memory must NOT appear — it only contains "unique", not "_unique_token"
        assert "other" not in projects

    def test_or_mode(self) -> None:
        result = MemoryStore._build_fts_query("credential masking", or_mode=True)
        assert result == "credential OR masking"


class TestGetByProject:
    def test_returns_project_memories(self, store: MemoryStore) -> None:
        store.add("alpha content one", "f.md", "alpha", "Config")
        store.add("alpha content two", "f.md", "alpha", "Security")
        store.add("beta content", "f.md", "beta", "Config")
        results = store.get_by_project("alpha")
        assert len(results) == 2
        assert all(m.project == "alpha" for m in results)

    def test_respects_limit(self, store: MemoryStore) -> None:
        for i in range(10):
            store.add(f"memory {i}", "f.md", "proj", "section")
        results = store.get_by_project("proj", limit=3)
        assert len(results) == 3

    def test_empty_project(self, store: MemoryStore) -> None:
        assert store.get_by_project("nonexistent") == []


class TestGetSharedSections:
    def test_finds_shared_sections(self, store: MemoryStore) -> None:
        store.add("alpha security", "f.md", "alpha", "Security")
        store.add("beta security", "f.md", "beta", "Security")
        store.add("beta deploy", "f.md", "beta", "Deployment")
        results = store.get_shared_sections("alpha")
        assert len(results) == 1
        assert results[0].project == "beta"
        assert results[0].section == "Security"

    def test_excludes_own_project(self, store: MemoryStore) -> None:
        store.add("alpha sec", "f.md", "alpha", "Security")
        store.add("beta sec", "f.md", "beta", "Security")
        results = store.get_shared_sections("alpha")
        assert all(m.project != "alpha" for m in results)

    def test_ignores_empty_sections(self, store: MemoryStore) -> None:
        store.add("alpha root", "f.md", "alpha", "")
        store.add("beta root", "f.md", "beta", "")
        results = store.get_shared_sections("alpha")
        assert len(results) == 0


class TestUpdate:
    def test_update_content(self, store: MemoryStore) -> None:
        mid = store.add("old content", "f.md", "alpha", "Config")
        assert store.update(mid, "new content") is True
        mem = store.get(mid)
        assert mem.content == "new content"
        assert mem.id == mid

    def test_update_preserves_section_and_project(self, store: MemoryStore) -> None:
        mid = store.add("original", "f.md", "alpha", "Security")
        store.update(mid, "updated")
        mem = store.get(mid)
        assert mem.project == "alpha"
        assert mem.section == "Security"

    def test_update_section(self, store: MemoryStore) -> None:
        mid = store.add("content", "f.md", "alpha", "Research")
        store.update(mid, "content v2", section="Experiments")
        mem = store.get(mid)
        assert mem.section == "Experiments"

    def test_update_project(self, store: MemoryStore) -> None:
        mid = store.add("content", "f.md", "alpha", "Config")
        store.update(mid, "content v2", project="beta")
        mem = store.get(mid)
        assert mem.project == "beta"

    def test_update_nonexistent(self, store: MemoryStore) -> None:
        assert store.update(9999, "anything") is False

    def test_update_refreshes_fts(self, store: MemoryStore) -> None:
        mid = store.add("old searchable keyword", "f.md", "alpha")
        store.update(mid, "new discoverable term")
        assert len(store.search("old")) == 0
        assert len(store.search("discoverable")) == 1


class TestDelete:
    def test_delete_by_id(self, store: MemoryStore) -> None:
        mid = store.add("to delete", "f.md", "alpha", "Config")
        assert store.count() == 1
        assert store.delete(mid) is True
        assert store.count() == 0

    def test_delete_nonexistent(self, store: MemoryStore) -> None:
        assert store.delete(9999) is False

    def test_delete_removes_from_fts(self, store: MemoryStore) -> None:
        mid = store.add("unique searchable content", "f.md", "alpha")
        assert len(store.search("unique")) == 1
        store.delete(mid)
        assert len(store.search("unique")) == 0

    def test_delete_by_project(self, store: MemoryStore) -> None:
        store.add("one", "f.md", "alpha")
        store.add("two", "f.md", "alpha")
        store.add("three", "f.md", "beta")
        deleted = store.delete_by_project("alpha")
        assert deleted == 2
        assert store.count() == 1

    def test_delete_by_project_empty(self, store: MemoryStore) -> None:
        assert store.delete_by_project("nonexistent") == 0


class TestGet:
    def test_get_existing(self, store: MemoryStore) -> None:
        mid = store.add("test content", "f.md", "alpha", "Config")
        mem = store.get(mid)
        assert mem is not None
        assert mem.content == "test content"
        assert mem.project == "alpha"

    def test_get_nonexistent(self, store: MemoryStore) -> None:
        assert store.get(9999) is None


class TestUpsert:
    def test_inserts_new(self, store: MemoryStore) -> None:
        result = store.upsert("content", "f.md", "proj", "Config")
        assert result is not None
        assert store.count() == 1

    def test_skips_identical(self, store: MemoryStore) -> None:
        store.upsert("content", "f.md", "proj", "Config")
        result = store.upsert("content", "f.md", "proj", "Config")
        assert result is None
        assert store.count() == 1

    def test_updates_changed_content(self, store: MemoryStore) -> None:
        mid = store.upsert("old content", "f.md", "proj", "Config")
        mid2 = store.upsert("new content", "f.md", "proj", "Config")
        assert mid2 == mid
        assert store.count() == 1
        mem = store.get(mid)
        assert mem.content == "new content"

    def test_preserves_id_on_update(self, store: MemoryStore) -> None:
        mid = store.upsert("v1", "init:README.md", "proj", "Arch")
        mid2 = store.upsert("v2", "init:README.md", "proj", "Arch")
        assert mid2 == mid

    def test_different_section_is_separate(self, store: MemoryStore) -> None:
        store.upsert("content a", "f.md", "proj", "Config")
        store.upsert("content b", "f.md", "proj", "Security")
        assert store.count() == 2

    def test_different_source_same_content_deduped(self, store: MemoryStore) -> None:
        store.upsert("content", "init:README.md", "proj", "Arch")
        result = store.upsert("content", "init:CLAUDE.md", "proj", "Arch")
        # Same content + project → deduped by content_hash constraint
        assert result is None
        assert store.count() == 1

    def test_updates_fts_index(self, store: MemoryStore) -> None:
        store.upsert("old searchable keyword", "f.md", "proj", "S")
        store.upsert("new discoverable term", "f.md", "proj", "S")
        assert len(store.search("old")) == 0
        assert len(store.search("discoverable")) == 1


class TestSchemaMigration:
    def test_fresh_db_gets_version_1(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "fresh.db")
        row = store.db.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()
        assert row["v"] == 1
        store.close()

    def test_preexisting_db_upgraded(self, tmp_path: Path) -> None:
        """A DB created before the migration table gets upgraded seamlessly."""
        import sqlite3

        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        # Simulate a legacy DB: create the tables without schema_version
        conn.executescript("""
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                source_file TEXT NOT NULL,
                project TEXT NOT NULL,
                section TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(content_hash, project)
            );
            CREATE VIRTUAL TABLE memories_fts USING fts5(
                content, project, section,
                content='memories', content_rowid='id',
                tokenize='porter unicode61'
            );
        """)
        conn.execute(
            "INSERT INTO memories (content, source_file, project, section, content_hash) "
            "VALUES ('legacy data', 'f.md', 'proj', '', 'abc123')"
        )
        conn.commit()
        conn.close()

        # Open with MemoryStore — should migrate without data loss
        store = MemoryStore(db_path=db_path)
        assert store.count() == 1
        mem = store.get(1)
        assert mem.content == "legacy data"
        row = store.db.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()
        assert row["v"] == 1
        store.close()

    def test_reopening_db_is_idempotent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "reopen.db"
        store1 = MemoryStore(db_path=db_path)
        store1.add("test", "f.md", "proj")
        store1.close()

        store2 = MemoryStore(db_path=db_path)
        assert store2.count() == 1
        store2.close()


class TestStats:
    def test_stats_by_project(self, store: MemoryStore) -> None:
        store.add("one", "f.md", "alpha")
        store.add("two", "f.md", "alpha")
        store.add("three", "f.md", "beta")
        stats = store.stats()
        assert stats["alpha"] == 2
        assert stats["beta"] == 1
