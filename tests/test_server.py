"""Tests for MCP server helpers."""

from pathlib import Path
from unittest.mock import patch

from crossmem.server import mem_save, resolve_project
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
        result = resolve_project(
            "/Users/foo/mobile-app/backend-api", KNOWN_PROJECTS
        )
        assert result == "backend-api"

    def test_suffix_match(self) -> None:
        result = resolve_project(
            "/Users/foo/my-auth-service", KNOWN_PROJECTS
        )
        assert result == "auth-service"

    def test_fuzzy_combined_segments(self) -> None:
        result = resolve_project(
            "/Users/foo/Documents/DS/WORKSPACE", KNOWN_PROJECTS
        )
        assert result == "DS-WORKSPACE"

    def test_exact_match_hyphenated_segment(self) -> None:
        result = resolve_project(
            "/Users/foo/ACME-dashboard/frontend", KNOWN_PROJECTS
        )
        assert result == "ACME-dashboard"

    def test_case_insensitive(self) -> None:
        result = resolve_project(
            "/Users/foo/Mobile-App", KNOWN_PROJECTS
        )
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
        import crossmem.server as srv

        self._store = MemoryStore(db_path=Path(":memory:"))
        self._original = srv._store
        srv._store = self._store

    def teardown_method(self) -> None:
        import crossmem.server as srv

        srv._store = self._original

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
