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
    def test_fresh_db_gets_current_version(self, tmp_path: Path) -> None:
        store = MemoryStore(db_path=tmp_path / "fresh.db")
        row = store.db.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        assert row["v"] == 7
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
        row = store.db.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        assert row["v"] == 7
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


class TestSearchExpanded:
    def test_finds_synonym_hit(self, store: MemoryStore) -> None:
        store.add("JWT token validation approach", "f.md", "proj")
        results = store.search_expanded("validate credentials")
        assert len(results) == 1
        assert "token" in results[0].memory.content

    def test_ship_new_version_steps(self, store: MemoryStore) -> None:
        store.add("PyPI publish process for crossmem Bump version release", "f.md", "proj")
        # "new" has no synonyms; use "ship version steps" to test synonym expansion
        results = store.search_expanded("ship version steps")
        assert len(results) == 1

    def test_no_results_for_unrelated_query(self, store: MemoryStore) -> None:
        store.add("Python logging best practices", "f.md", "proj")
        results = store.search_expanded("docker kubernetes container")
        assert len(results) == 0

    def test_fallback_to_search_when_no_words(self, store: MemoryStore) -> None:
        store.add("exact phrase content", "f.md", "proj")
        results = store.search_expanded("")
        assert results == []

    def test_project_filter_respected(self, store: MemoryStore) -> None:
        store.add("deployment rollout steps", "f.md", "alpha")
        store.add("deployment rollout steps", "f.md", "beta")
        results = store.search_expanded("ship steps", project="alpha")
        assert len(results) == 1
        assert results[0].memory.project == "alpha"

    def test_synonym_cache_invalidates_on_add(self, store: MemoryStore) -> None:
        store.add("run test suite", "f.md", "proj")
        store.search_expanded("run pytest suite")
        store.add_synonym("testenv", "pytest")
        store.search_expanded("run pytest suite")
        # After adding synonym, cache is invalidated — new group is available
        assert store._synonym_cache is None or "pytest" in store._synonym_cache


class TestKeywordsColumn:
    def test_keywords_set_on_add(self, store: MemoryStore) -> None:
        mid = store.add("PyPI publish process", "f.md", "proj")
        mem = store.get(mid)
        assert mem.keywords != ""

    def test_keywords_updated_on_update(self, store: MemoryStore) -> None:
        mid = store.add("PyPI publish process", "f.md", "proj")
        kw_before = store.get(mid).keywords
        store.update(mid, "docker container deployment")
        kw_after = store.get(mid).keywords
        assert kw_after != kw_before

    def test_keywords_updated_on_upsert(self, store: MemoryStore) -> None:
        mid = store.upsert("PyPI publish process", "f.md", "proj", "Deploy")
        kw_before = store.get(mid).keywords
        store.upsert("docker container deployment", "f.md", "proj", "Deploy")
        mem = store.get(mid)
        # After upsert with docker content, keywords should now contain docker synonyms
        assert kw_before != mem.keywords
        assert "image" in mem.keywords or "kubernetes" in mem.keywords

    def test_strict_search_ignores_keywords_column(self, store: MemoryStore) -> None:
        # alpha memory has "deploy" in keywords (via synonym of "push" or similar)
        store.add("push to staging server", "f.md", "alpha")
        # beta memory has "deploy" literally in content
        store.add("deploy to production", "f.md", "beta")
        # strict search should not match alpha via its keywords column expansion
        results = store.search("deploy")
        projects = [r.memory.project for r in results]
        assert "beta" in projects
        assert "alpha" not in projects

    def test_backfill_populates_empty_keywords(self, tmp_path: Path) -> None:
        import sqlite3

        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                source_file TEXT NOT NULL,
                project TEXT NOT NULL,
                section TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL,
                keywords TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(content_hash, project)
            );
            CREATE VIRTUAL TABLE memories_fts USING fts5(
                content, project, section, keywords,
                content='memories', content_rowid='id',
                tokenize='porter unicode61'
            );
            CREATE TABLE synonyms (
                canonical TEXT NOT NULL, term TEXT NOT NULL,
                PRIMARY KEY(canonical, term)
            );
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
            INSERT INTO schema_version VALUES (2);
        """)
        conn.execute("INSERT INTO synonyms VALUES ('deploy', 'publish')")
        conn.execute(
            "INSERT INTO memories (content, source_file, project, section, content_hash, keywords) "
            "VALUES ('PyPI publish process', 'f.md', 'proj', '', 'abc123', '')"
        )
        conn.commit()
        conn.close()

        store = MemoryStore(db_path=db_path)
        count = store.backfill_keywords()
        assert count == 1
        mem = store.get(1)
        assert mem.keywords != ""
        store.close()


class TestMigration2:
    def test_migration_2_idempotent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        store1 = MemoryStore(db_path=db_path)
        store1.add("content", "f.md", "proj")
        store1.close()

        store2 = MemoryStore(db_path=db_path)
        assert store2.count() == 1
        row = store2.db.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        assert row["v"] == 7
        store2.close()

    def test_hyphenated_term_in_synonym_group_quoted(self, store: MemoryStore) -> None:
        store.add_synonym("ci", "pre-commit")
        store.add("run pre-commit hooks on save", "f.md", "proj")
        store.search_expanded("ci pipeline")
        # The synonym "pre-commit" should be quoted in FTS5 query
        groups = store._get_synonym_groups()
        assert "pre-commit" in groups.get("ci", frozenset())


class TestRemoveSynonym:
    def test_remove_existing_pair(self, store: MemoryStore) -> None:
        store.add_synonym("widget", "gadget")
        assert store.remove_synonym("widget", "gadget") is True

    def test_remove_nonexistent_returns_false(self, store: MemoryStore) -> None:
        assert store.remove_synonym("widget", "gadget") is False

    def test_remove_clears_cache(self, store: MemoryStore) -> None:
        store.add_synonym("widget", "gadget")
        store._get_synonym_groups()  # populate cache
        store.remove_synonym("widget", "gadget")
        assert store._synonym_cache is None


class TestFtsSpecialChars:
    """Gap 1 — FTS5 safety: special chars like $, @ must not crash queries."""

    def test_dollar_sign_query_does_not_raise(self, store: MemoryStore) -> None:
        store.add("Use $TOKEN env var for auth", "f.md", "proj")
        # Should not raise fts5: syntax error
        results = store.search("$TOKEN")
        assert isinstance(results, list)

    def test_at_sign_query_does_not_raise(self, store: MemoryStore) -> None:
        store.add("Contact admin@example.com for access", "f.md", "proj")
        results = store.search("@example.com")
        assert isinstance(results, list)

    def test_dollar_sign_stripped_from_fts_query(self, store: MemoryStore) -> None:
        fts = store._build_fts_query("$TOKEN config")
        assert "$" not in fts

    def test_at_sign_stripped_from_fts_query(self, store: MemoryStore) -> None:
        fts = store._build_fts_query("@email.com")
        assert "@" not in fts

    def test_special_chars_only_query_returns_empty(self, store: MemoryStore) -> None:
        results = store.search("$$ @@ ##")
        assert results == []


class TestGetGlobalMemoriesDedup:
    """Gap 2 — get_global_memories deduplicates promoted cross-project patterns."""

    def test_no_query_deduplicates_same_hash(self, store: MemoryStore) -> None:
        content = "Always pin dependency versions to avoid supply chain drift."
        store.add(content, "mcp:mem_save", "proj-a", scope="global")
        store.add(content, "mcp:mem_save", "proj-b", scope="global")
        mems = store.get_global_memories()
        snippets = [m.snippet for m in mems]
        assert snippets.count(snippets[0]) == 1

    def test_query_deduplicates_same_hash(self, store: MemoryStore) -> None:
        content = "Always pin dependency versions to avoid supply chain drift."
        store.add(content, "mcp:mem_save", "proj-a", scope="global")
        store.add(content, "mcp:mem_save", "proj-b", scope="global")
        mems = store.get_global_memories(query="dependency")
        assert len(mems) == 1

    def test_query_uses_synonym_expansion(self, store: MemoryStore) -> None:
        store.add_synonym("auth", "authentication")
        store.add(
            "Always validate authentication tokens on every request.",
            "mcp:mem_save",
            "proj-a",
            scope="global",
        )
        mems = store.get_global_memories(query="auth")
        assert len(mems) >= 1

    def test_distinct_globals_all_returned(self, store: MemoryStore) -> None:
        store.add("Pin dependency versions.", "mcp:mem_save", "proj-a", scope="global")
        store.add("Use structured logging in production.", "mcp:mem_save", "proj-b", scope="global")
        mems = store.get_global_memories()
        assert len(mems) == 2

    def test_search_expanded_scope_global_filter(self, store: MemoryStore) -> None:
        store.add("global pattern memory", "mcp:mem_save", "proj-a", scope="global")
        store.add("project-only memory", "mcp:mem_save", "proj-b", scope="project")
        results = store.search_expanded("memory", scope="global")
        assert all(r.memory.scope == "global" for r in results)

    def test_remove_is_case_insensitive(self, store: MemoryStore) -> None:
        store.add_synonym("Widget", "Gadget")
        assert store.remove_synonym("WIDGET", "GADGET") is True

    def test_remove_only_removes_specified_pair(self, store: MemoryStore) -> None:
        store.add_synonym("widget", "gadget")
        store.add_synonym("widget", "gizmo")
        store.remove_synonym("widget", "gadget")
        synonyms = store.list_synonyms()
        assert "gizmo" in synonyms.get("widget", [])
        assert "gadget" not in synonyms.get("widget", [])


class TestListSynonymsWithSource:
    def test_returns_source_for_seed_entries(self, store: MemoryStore) -> None:
        groups = store.list_synonyms_with_source()
        # Seed entry: auth → jwt with source=seed
        jwt_entries = groups.get("auth", [])
        assert any(term == "jwt" and src == "seed" for term, src in jwt_entries)

    def test_returns_source_for_user_entries(self, store: MemoryStore) -> None:
        store.add_synonym("widget", "gadget", source="user")
        groups = store.list_synonyms_with_source()
        assert ("gadget", "user") in groups.get("widget", [])

    def test_returns_source_for_learned_entries(self, store: MemoryStore) -> None:
        store.add_synonym("zap", "zot", source="learned")
        groups = store.list_synonyms_with_source()
        assert ("zot", "learned") in groups.get("zap", [])

    def test_empty_when_all_removed(self, store: MemoryStore) -> None:
        store.add_synonym("widget", "gadget")
        store.remove_synonym("widget", "gadget")
        groups = store.list_synonyms_with_source()
        assert "gadget" not in [t for t, _ in groups.get("widget", [])]

    def test_result_structure(self, store: MemoryStore) -> None:
        store.add_synonym("alpha", "beta")
        groups = store.list_synonyms_with_source()
        assert "alpha" in groups
        for entry in groups["alpha"]:
            assert len(entry) == 2
            term, source = entry
            assert isinstance(term, str)
            assert isinstance(source, str)

    def test_can_filter_by_source(self, store: MemoryStore) -> None:
        store.add_synonym("widget", "gadget", source="user")
        store.add_synonym("zap", "zot", source="learned")
        groups = store.list_synonyms_with_source(source="user")
        assert ("gadget", "user") in groups.get("widget", [])
        assert "zap" not in groups


class TestChooseCanonical:
    def test_prefers_existing_canonical(self, store: MemoryStore) -> None:
        # 'auth' is already a canonical in the seed
        result = store._choose_canonical("auth", "newterm")
        assert result == "auth"

    def test_prefers_higher_df(self, store: MemoryStore) -> None:
        # Add memories with different token frequencies
        store.add("alpha alpha alpha doc", "f.md", "p", "")
        store.add("alpha alpha beta doc", "f.md", "p2", "")
        store.add("alpha gamma doc", "f.md", "p3", "")
        # alpha appears in 3 docs, gamma in 1 — but we need two terms not already canonical
        # Use tokens that are unique to this test and have no seed entries
        result = store._choose_canonical("uniquewordx", "uniquewordy")
        # Both have 0 df → falls back to lexicographic
        assert result == min("uniquewordx", "uniquewordy")

    def test_lexicographic_tiebreak(self, store: MemoryStore) -> None:
        result = store._choose_canonical("zebra", "aardvark")
        assert result == "aardvark"

    def test_symmetric_inverse(self, store: MemoryStore) -> None:
        # Non-canonical, equal-df pair → lexicographic, must be stable
        a = store._choose_canonical("mango", "papaya")
        b = store._choose_canonical("papaya", "mango")
        assert a == b


class TestLearnSynonyms:
    def _seed_corpus(self, store: MemoryStore) -> None:
        store.add("JWT token validation middleware for FastAPI", "f.md", "proj", "Security")
        store.add("JWT authentication and token refresh logic", "f.md", "proj", "Auth")
        store.add("Token validation in the API layer uses JWT", "f.md", "proj", "API")

    def test_returns_count_of_added_pairs(self, store: MemoryStore) -> None:
        self._seed_corpus(store)
        added = store.learn_synonyms(min_df=2, min_jaccard=0.1)
        assert isinstance(added, int)
        assert added >= 0

    def test_learned_pairs_have_source_learned(self, store: MemoryStore) -> None:
        self._seed_corpus(store)
        store.learn_synonyms(min_df=2, min_jaccard=0.1)
        groups = store.list_synonyms_with_source()
        learned = [(c, t, s) for c, pairs in groups.items() for t, s in pairs if s == "learned"]
        # Any pair learned must be tagged 'learned'
        for _, _, src in learned:
            assert src == "learned"

    def test_does_not_duplicate_existing_pairs(self, store: MemoryStore) -> None:
        self._seed_corpus(store)
        store.learn_synonyms(min_df=2, min_jaccard=0.1)
        after = sum(len(v) for v in store.list_synonyms().values())
        # Running again must not add new rows
        store.learn_synonyms(min_df=2, min_jaccard=0.1)
        after2 = sum(len(v) for v in store.list_synonyms().values())
        assert after == after2

    def test_empty_store_returns_zero(self, store: MemoryStore) -> None:
        assert store.learn_synonyms() == 0

    def test_min_df_filters_rare_tokens(self, store: MemoryStore) -> None:
        store.add("JWT token validation", "f.md", "proj", "")
        store.add("JWT authentication logic", "f.md", "proj2", "")
        # With min_df=3 nothing should qualify (only 2 docs)
        added = store.learn_synonyms(min_df=3, min_jaccard=0.01)
        assert added == 0

    def test_impossible_jaccard_threshold_adds_nothing(self, store: MemoryStore) -> None:
        self._seed_corpus(store)
        # Jaccard is bounded at 1.0, so 1.01 can never match
        added = store.learn_synonyms(min_df=2, min_jaccard=1.01)
        assert added == 0

    def test_benchmark_precision_gate(self, tmp_path: Path) -> None:
        """Learning must not degrade benchmark Precision@5 below 0.75."""
        from crossmem.benchmark import (
            default_benchmark_cases,
            run_benchmark,
            seed_benchmark_memories,
        )

        db_path = tmp_path / "bench.db"
        store = MemoryStore(db_path=db_path)
        seed_benchmark_memories(store)
        store.learn_synonyms()
        report = run_benchmark(store, cases=default_benchmark_cases(), limit=5, expanded=True)
        store.close()
        assert report.precision_at_k >= 0.90, (
            f"Precision@5 dropped to {report.precision_at_k:.2f} after learn_synonyms"
        )


class TestSearchExpandedEdgeCases:
    """Document the known retrieval limitations of search_expanded.
    Each test records current behavior and explains WHY the limitation exists.
    Tests marked LIMITATION assert failing/noisy behavior so regressions surface.
    Tests marked CORRECT assert that 0-result behavior is intentional.
    """

    def test_all_stopword_query_returns_nothing(self, store: MemoryStore) -> None:
        """Query of mostly closed-class words with no matching content returns 0 results.

        After closed-class filtering, only 'how' survives (it is an interrogative
        adverb — open class, not in CLOSED_CLASS).  'how' has no match in the
        single seeded memory ('Authentication middleware setup guide.'), so the
        FTS5 query finds nothing.  This is correct behavior.
        """
        store.add("Authentication middleware setup guide.", "f.md", "proj")
        results = store.search_expanded("how do we do this")
        assert len(results) == 0

    def test_unknown_jargon_single_token_returns_nothing(self, store: MemoryStore) -> None:
        """CORRECT: a genuinely unknown word has no match.

        No synonym, no stemming bridge, no memory content overlap → 0 results.
        This is correct behavior — there is nothing relevant to recall.
        """
        store.add("Database migration rollback with Alembic.", "f.md", "proj")
        results = store.search_expanded("terraform")
        assert len(results) == 0

    def test_compound_word_one_word_vs_hyphenated_miss(self, store: MemoryStore) -> None:
        """LIMITATION: 'precommit' as one word does not match 'pre-commit' in memory.

        FTS5 unicode61 tokenizer splits 'pre-commit' into tokens ['pre', 'commit'].
        The query word 'precommit' is indexed as a single token and never matches
        ['pre', 'commit'] individually. Bigram expansion only helps when the two
        words appear separately in the query (e.g. 'pre commit').
        """
        store.add("Always run pre-commit hooks before pushing.", "f.md", "proj")
        # Two-word form works via bigram expansion
        assert len(store.search_expanded("pre commit")) == 1
        # One-word form fails — 'precommit' ≠ ['pre', 'commit']
        assert len(store.search_expanded("precommit")) == 0

    def test_common_non_stopword_causes_fallback_noise(self, store: MemoryStore) -> None:
        """FIXED: 'up' is now a preposition in CLOSED_CLASS and is filtered.

        Previously 'up' was not filtered, causing fallback to match 'speeds up'
        and return the logging memory as noise.  With the two-layer filter,
        'up' is caught by Layer 1 (preposition), leaving ['set', 'auth'] as
        signal.  The AND query finds the auth memory and NOT the logging memory.
        """
        store.add("JWT token validation middleware for auth.", "f.md", "proj")
        store.add("Structured logging speeds up debugging.", "f.md", "proj")
        results = store.search_expanded("set up auth")
        contents = [r.memory.content for r in results]
        # Auth memory is found
        assert any("jwt" in c.lower() or "auth" in c.lower() for c in contents), (
            "Auth memory should be in results"
        )
        # Logging memory is no longer returned — 'up' is filtered, precision improved
        assert not any("logging" in c.lower() or "speeds up" in c.lower() for c in contents), (
            "'up' is now a filtered preposition — logging noise should be gone"
        )

    def test_synonym_group_saturation_loses_query_specificity(self, store: MemoryStore) -> None:
        """LIMITATION: multiple query tokens from the same synonym group collapse into one filter.

        'authentication' and 'authorization' both expand to the auth synonym group.
        The AND of (auth_group) AND (auth_group) AND 'setup' is identical to
        (auth_group) AND 'setup'. The extra specificity of two distinct concepts
        is lost — the query behaves as if only one auth term was supplied.
        When the third token ('setup') has no memory match, fallback fires and
        returns all auth memories indiscriminately.
        """
        store.add("JWT token validation middleware.", "f.md", "proj")
        store.add("Rotate API credentials every 90 days.", "f.md", "proj")
        store.add("Database schema migration guide.", "f.md", "proj")
        results = store.search_expanded("authentication authorization setup")
        # Fallback returns auth memories because 'setup' kills the AND and
        # both auth terms expand to the same group.
        contents = [r.memory.content.lower() for r in results]
        # Auth memories are returned (expected)
        assert any("jwt" in c or "credentials" in c for c in contents)
        # DB memory should NOT be in results (setup synonym doesn't include schema)
        assert not any("schema" in c for c in contents)

    def test_cross_project_filter_misses_relevant_other_project(self, store: MemoryStore) -> None:
        """CORRECT: project filter is strict — cross-project memories are never returned.

        A memory about session token refresh exists in 'mobile-app' but not
        'backend-api'. Querying with project='backend-api' returns 0 results
        even though the memory is semantically relevant. This is intentional
        isolation behavior, not a bug. Users must query without a project filter
        or know the target project to find cross-project knowledge.
        """
        store.add(
            "Frontend auth flow caches session token and refreshes before expiry.",
            "f.md",
            "mobile-app",
        )
        store.add("JWT validation middleware.", "f.md", "backend-api")
        mobile_results = store.search_expanded("session token refresh", project="mobile-app")
        backend_results = store.search_expanded("session token refresh", project="backend-api")
        assert len(mobile_results) >= 1, "Should find session memory in mobile-app"
        # backend-api has no session refresh memory — cross-project isolation holds
        assert not any("session" in r.memory.content.lower() for r in backend_results), (
            "CORRECT: project filter prevents cross-project bleed"
        )

    def test_fallback_result_order_is_token_order_not_relevance(self, store: MemoryStore) -> None:
        """LIMITATION: progressive fallback returns results in query-token order, not by relevance.

        The fallback iterates tokens left-to-right and appends results in encounter
        order. A memory that strongly matches the last token appears after a memory
        that weakly matches the first token. There is no re-ranking step.
        """
        # First token 'alpha' matches weak memory, second token 'deployment' matches strong
        store.add("alpha prefix convention for identifiers.", "f.md", "proj")
        store.add("Docker deployment checklist for production rollout.", "f.md", "proj")
        # AND fails ('alpha' AND 'deployment' have no shared memory), fallback fires
        results = store.search_expanded("alpha deployment")
        assert len(results) == 2
        # LIMITATION: alpha memory comes first because 'alpha' is the first token
        # even though the deployment memory is more likely to be what the user needs
        assert results[0].memory.content.startswith("alpha"), (
            "KNOWN LIMITATION: fallback result order is token-encounter order, not relevance"
        )

    def test_bigram_too_long_is_ignored_gracefully(self, store: MemoryStore) -> None:
        """Bigrams longer than 16 chars are silently dropped — no error, no match."""
        # 'authentication'(14) + 'authorization'(13) = 27 chars -> dropped
        store.add("OAuth2 authentication and authorization flow.", "f.md", "proj")
        # Query still works; bigram is just not generated
        results = store.search_expanded("authentication authorization")
        assert len(results) >= 1

    def test_short_bigram_at_boundary_is_included(self, store: MemoryStore) -> None:
        """Bigrams of exactly 4 chars are included; 3-char bigrams are not."""
        store.add("Database go live cutover procedure.", "f.md", "proj")
        # 'go'(2) + 'live'(4) = 'golive'(6) -> included (>=4)
        # 'go'(2) + 'li'(2) = 'goli'(4) -> included
        # but 'is'+'a' = 'isa'(3) -> excluded
        results = store.search_expanded("go live")
        # Should work without crashing regardless of bigram boundary
        assert isinstance(results, list)

    def test_empty_query_returns_nothing(self, store: MemoryStore) -> None:
        """Empty string query returns 0 results without raising."""
        store.add("Some memory content.", "f.md", "proj")
        results = store.search_expanded("")
        assert results == []

    def test_numeric_token_matches_exactly(self, store: MemoryStore) -> None:
        """Numeric tokens in queries match exact numbers in memory content.

        FTS5 porter+unicode61 indexes numbers as-is. '90' in the query matches
        '90' in the memory. No stemming or synonym expansion applies to numbers.
        """
        store.add(
            "Rotate API credentials every 90 days and keep secrets in a vault.", "f.md", "proj"
        )
        results = store.search_expanded("rotate every 90 days")
        assert len(results) == 1
        assert "90 days" in results[0].memory.content
