"""Tests for MCP server helpers."""

from pathlib import Path
from unittest.mock import patch

from crossmem.server import mem_forget, mem_get, mem_save, mem_update, resolve_project
from crossmem.store import MemoryStore

KNOWN_PROJECTS = [
    "backend-api",
    "mobile-app",
    "data-pipeline",
    "ACME-dashboard",
    "infra-tools",
    "DS-WORKSPACE",
    "auth-service",
]


class TestResolveProject:
    def test_exact_match_last_segment(self) -> None:
        result = resolve_project("/Users/foo/Documents/backend-api", KNOWN_PROJECTS)
        assert result == "backend-api"

    def test_exact_match_middle_segment(self) -> None:
        result = resolve_project("/Users/foo/backend-api/src", KNOWN_PROJECTS)
        assert result == "backend-api"

    def test_prefers_rightmost_segment(self) -> None:
        result = resolve_project("/Users/foo/mobile-app/backend-api", KNOWN_PROJECTS)
        assert result == "backend-api"

    def test_suffix_match(self) -> None:
        result = resolve_project("/Users/foo/my-auth-service", KNOWN_PROJECTS)
        assert result == "auth-service"

    def test_fuzzy_combined_segments(self) -> None:
        result = resolve_project("/Users/foo/Documents/DS/WORKSPACE", KNOWN_PROJECTS)
        assert result == "DS-WORKSPACE"

    def test_exact_match_hyphenated_segment(self) -> None:
        result = resolve_project("/Users/foo/ACME-dashboard/frontend", KNOWN_PROJECTS)
        assert result == "ACME-dashboard"

    def test_case_insensitive(self) -> None:
        result = resolve_project("/Users/foo/Mobile-App", KNOWN_PROJECTS)
        assert result == "mobile-app"

    def test_underscore_matches_hyphen(self) -> None:
        result = resolve_project("/Users/foo/DS_WORKSPACE", KNOWN_PROJECTS)
        assert result == "DS-WORKSPACE"

    def test_no_match_returns_none(self) -> None:
        result = resolve_project("/Users/foo/unknown-project", KNOWN_PROJECTS)
        assert result is None

    def test_empty_projects_returns_none(self) -> None:
        result = resolve_project("/Users/foo/something", [])
        assert result is None

    def test_root_path(self) -> None:
        result = resolve_project("/", KNOWN_PROJECTS)
        assert result is None


class TestMemSave:
    def setup_method(self) -> None:

        self._store = MemoryStore(db_path=Path(":memory:"))
        self._store.close = lambda: None  # no-op: tools call close() after each call
        self._patcher = patch("crossmem.server.get_store", return_value=self._store)
        self._patcher.start()

    def teardown_method(self) -> None:
        self._patcher.stop()

    def test_save_with_explicit_project(self) -> None:
        result = mem_save(content="Use retry with backoff", project="my-app")
        assert "Saved to 'my-app'" in result
        assert self._store.count() == 1

    def test_save_with_section(self) -> None:
        result = mem_save(
            content="Always validate JWT expiry",
            section="Security",
            project="my-app",
        )
        assert "Security" in result
        assert self._store.count() == 1
        memories = self._store.get_by_project("my-app")
        assert memories[0].section == "Security"

    def test_save_deduplicates(self) -> None:
        mem_save(content="Same content here", project="my-app")
        result = mem_save(content="Same content here", project="my-app")
        assert "already exists" in result
        assert self._store.count() == 1

    def test_save_auto_detects_project(self) -> None:
        self._store.add("existing", "file.md", "backend-api", "")
        with patch("crossmem.server.os.getcwd", return_value="/Users/foo/backend-api"):
            result = mem_save(content="New discovery about auth")
        assert "Saved to 'backend-api'" in result

    def test_save_derives_project_from_cwd_when_unknown(self) -> None:
        result = mem_save(
            content="Something new",
            cwd="/Users/foo/brand_new_project",
        )
        assert "Saved to 'brand-new-project'" in result

    def test_save_source_file_is_mcp(self) -> None:
        mem_save(content="Test source tracking", project="my-app")
        memories = self._store.get_by_project("my-app")
        assert memories[0].source_file == "mcp:mem_save"


class TestMemForget:
    def setup_method(self) -> None:

        self._store = MemoryStore(db_path=Path(":memory:"))
        self._store.close = lambda: None  # no-op: tools call close() after each call
        self._patcher = patch("crossmem.server.get_store", return_value=self._store)
        self._patcher.start()

    def teardown_method(self) -> None:
        self._patcher.stop()

    def test_forget_by_id(self) -> None:
        mem_save(content="Will be deleted", project="my-app")
        memories = self._store.get_by_project("my-app")
        result = mem_forget(memory_id=memories[0].id)
        assert "Deleted memory" in result
        assert self._store.count() == 0

    def test_forget_nonexistent(self) -> None:
        result = mem_forget(memory_id=9999)
        assert "not found" in result

    def test_forget_shows_context(self) -> None:
        mem_save(content="Important security pattern", section="Security", project="my-app")
        memories = self._store.get_by_project("my-app")
        result = mem_forget(memory_id=memories[0].id)
        assert "my-app" in result
        assert "Security" in result


class TestMemUpdate:
    def setup_method(self) -> None:

        self._store = MemoryStore(db_path=Path(":memory:"))
        self._store.close = lambda: None  # no-op: tools call close() after each call
        self._patcher = patch("crossmem.server.get_store", return_value=self._store)
        self._patcher.start()

    def teardown_method(self) -> None:
        self._patcher.stop()

    def test_update_content(self) -> None:
        mem_save(content="old info", project="my-app", section="Config")
        memories = self._store.get_by_project("my-app")
        result = mem_update(memory_id=memories[0].id, content="new info")
        assert "Updated memory" in result
        assert "my-app" in result
        mem = self._store.get(memories[0].id)
        assert mem.content == "new info"

    def test_update_section(self) -> None:
        mem_save(content="misplaced", project="my-app", section="Research")
        memories = self._store.get_by_project("my-app")
        result = mem_update(memory_id=memories[0].id, content="corrected", section="Experiments")
        assert "Experiments" in result
        mem = self._store.get(memories[0].id)
        assert mem.section == "Experiments"

    def test_update_nonexistent(self) -> None:
        result = mem_update(memory_id=9999, content="anything")
        assert "not found" in result

    def test_update_preserves_id(self) -> None:
        mem_save(content="original", project="my-app")
        memories = self._store.get_by_project("my-app")
        original_id = memories[0].id
        mem_update(memory_id=original_id, content="updated")
        mem = self._store.get(original_id)
        assert mem is not None
        assert mem.id == original_id


class TestMemGet:
    def setup_method(self) -> None:

        self._store = MemoryStore(db_path=Path(":memory:"))
        self._store.close = lambda: None  # no-op: tools call close() after each call
        self._patcher = patch("crossmem.server.get_store", return_value=self._store)
        self._patcher.start()

    def teardown_method(self) -> None:
        self._patcher.stop()

    def test_get_returns_full_content(self) -> None:
        long_content = "A" * 500
        mem_save(content=long_content, project="my-app", section="Architecture")
        memories = self._store.get_by_project("my-app")
        result = mem_get(memory_id=memories[0].id)
        assert long_content in result
        assert "my-app / Architecture" in result

    def test_get_nonexistent(self) -> None:
        result = mem_get(memory_id=9999)
        assert "not found" in result

    def test_get_without_section(self) -> None:
        mem_save(content="simple memory", project="my-app")
        memories = self._store.get_by_project("my-app")
        result = mem_get(memory_id=memories[0].id)
        assert "simple memory" in result
        assert "my-app" in result
