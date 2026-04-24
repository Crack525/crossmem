"""SQLite + FTS5 memory store."""

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Memory:
    """A single memory entry."""

    id: int
    content: str
    source_file: str
    project: str
    section: str
    content_hash: str
    created_at: str

    @property
    def snippet(self) -> str:
        """First 200 chars for display."""
        text = self.content.strip()
        return text[:200] + "..." if len(text) > 200 else text


@dataclass
class SearchResult:
    """A search result with ranking info."""

    memory: Memory
    rank: float
    highlight: str


DEFAULT_DB_PATH = Path(os.environ.get("CROSSMEM_DB", Path.home() / ".crossmem" / "crossmem.db"))


class MemoryStore:
    """SQLite + FTS5 backed memory store."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path), timeout=10)
        self.db.row_factory = sqlite3.Row
        # WAL allows concurrent readers + one writer; upgrade silently
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass  # locked by another connection — existing mode is fine
        self.db.execute("PRAGMA busy_timeout=30000")
        self._init_schema()

    # -- Schema migrations ------------------------------------------------
    # Each entry is (version, sql).  Versions are applied in order.
    # Existing databases that pre-date the migration table are treated as
    # version 0 (the original schema already exists; migration 1 is a no-op
    # for them because every statement uses IF NOT EXISTS).
    _MIGRATIONS: list[tuple[int, str]] = [
        (
            1,
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                source_file TEXT NOT NULL,
                project TEXT NOT NULL,
                section TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(content_hash, project)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                content,
                project,
                section,
                content='memories',
                content_rowid='id',
                tokenize='porter unicode61'
            );

            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content, project, section)
                VALUES (new.id, new.content, new.project, new.section);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, project, section)
                VALUES ('delete', old.id, old.content, old.project, old.section);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, project, section)
                VALUES ('delete', old.id, old.content, old.project, old.section);
                INSERT INTO memories_fts(rowid, content, project, section)
                VALUES (new.id, new.content, new.project, new.section);
            END;
        """,
        ),
    ]

    def _init_schema(self) -> None:
        # Ensure the version-tracking table exists
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            )
        """)
        row = self.db.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        current = row["v"] or 0

        for version, sql in self._MIGRATIONS:
            if version > current:
                self.db.executescript(sql)
                self.db.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (version,),
                )
                self.db.commit()

    def add(
        self,
        content: str,
        source_file: str,
        project: str,
        section: str = "",
    ) -> int | None:
        """Add a memory. Returns id if new, None if duplicate."""
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        try:
            cursor = self.db.execute(
                """INSERT INTO memories (content, source_file, project, section, content_hash)
                   VALUES (?, ?, ?, ?, ?)""",
                (content, source_file, project, section, content_hash),
            )
            self.db.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            return None

    def upsert(
        self,
        content: str,
        source_file: str,
        project: str,
        section: str = "",
    ) -> int | None:
        """Add or update a memory. Matches on (project, section, source_file).

        If a memory with the same key exists:
        - Same content → no-op, returns None
        - Different content → updates in place, returns existing id
        If no match → inserts new, returns new id
        """
        row = self.db.execute(
            """SELECT id, content FROM memories
               WHERE project = ? AND section = ? AND source_file = ?""",
            (project, section, source_file),
        ).fetchone()

        if row:
            if row["content"] == content:
                return None
            content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
            try:
                self.db.execute(
                    """UPDATE memories
                       SET content = ?, content_hash = ?
                       WHERE id = ?""",
                    (content, content_hash, row["id"]),
                )
                self.db.commit()
                return row["id"]
            except sqlite3.IntegrityError:
                # New content_hash already exists for this project — treat as no-op
                return None

        return self.add(content, source_file, project, section)

    def search(
        self,
        query: str,
        limit: int = 10,
        project: str | None = None,
        or_mode: bool = False,
    ) -> list[SearchResult]:
        """Full-text search across all memories.

        Query logic:
        - Multiple words default to AND (all terms must match)
        - Quoted phrases are preserved as exact matches
        - Single words search as-is
        - or_mode=True uses OR logic (any term can match) — useful for fuzzy/prompt searches
        """
        fts_query = self._build_fts_query(query, or_mode=or_mode)

        sql = """SELECT m.*, rank, highlight(memories_fts, 0, '>>>', '<<<') as hl
                 FROM memories_fts fts
                 JOIN memories m ON m.id = fts.rowid
                 WHERE memories_fts MATCH ?"""
        params: list[str | int] = [fts_query]

        if project:
            sql += " AND m.project = ?"
            params.append(project)

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        rows = self.db.execute(sql, params).fetchall()

        return [
            SearchResult(
                memory=Memory(
                    id=row["id"],
                    content=row["content"],
                    source_file=row["source_file"],
                    project=row["project"],
                    section=row["section"],
                    content_hash=row["content_hash"],
                    created_at=row["created_at"],
                ),
                rank=row["rank"],
                highlight=row["hl"],
            )
            for row in rows
        ]

    @staticmethod
    def _build_fts_query(query: str, *, or_mode: bool = False) -> str:
        """Build FTS5 query string. Default to AND logic, preserve quoted phrases."""
        import re

        # Extract quoted phrases first
        phrases = re.findall(r'"([^"]+)"', query)
        remaining = re.sub(r'"[^"]*"', "", query).strip()

        parts = [f'"{p}"' for p in phrases]
        if remaining:
            words = remaining.split()
            # Quote hyphenated or underscored words — FTS5 interprets "-" as column
            # filter and "_" as token separator (unicode61 tokenizer), both cause
            # incorrect query expansion or wrong token splitting.
            parts.extend(f'"{w}"' if ("-" in w or "_" in w) else w for w in words)

        if not parts:
            return query

        joiner = " OR " if or_mode else " AND "
        return joiner.join(parts)

    def get_by_project(self, project: str, limit: int = 50) -> list[Memory]:
        """Get all memories for a project, ordered by most recent."""
        rows = self.db.execute(
            "SELECT * FROM memories WHERE project = ? ORDER BY created_at DESC LIMIT ?",
            (project, limit),
        ).fetchall()
        return [
            Memory(
                id=row["id"],
                content=row["content"],
                source_file=row["source_file"],
                project=row["project"],
                section=row["section"],
                content_hash=row["content_hash"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_shared_sections(self, project: str, limit: int = 20) -> list[Memory]:
        """Get memories from other projects that share section names with this project."""
        rows = self.db.execute(
            """SELECT m.* FROM memories m
               WHERE m.project != ?
               AND m.section != ''
               AND m.section IN (
                   SELECT DISTINCT section FROM memories WHERE project = ? AND section != ''
               )
               ORDER BY m.section, m.project
               LIMIT ?""",
            (project, project, limit),
        ).fetchall()
        return [
            Memory(
                id=row["id"],
                content=row["content"],
                source_file=row["source_file"],
                project=row["project"],
                section=row["section"],
                content_hash=row["content_hash"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def update(
        self,
        memory_id: int,
        content: str,
        section: str | None = None,
        project: str | None = None,
    ) -> bool:
        """Update a memory in place. Returns True if updated."""
        mem = self.get(memory_id)
        if not mem:
            return False

        new_section = section if section is not None else mem.section
        new_project = project if project is not None else mem.project
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        self.db.execute(
            """UPDATE memories
               SET content = ?, section = ?, project = ?, content_hash = ?
               WHERE id = ?""",
            (content, new_section, new_project, content_hash, memory_id),
        )
        self.db.commit()
        return True

    def delete(self, memory_id: int) -> bool:
        """Delete a memory by ID. Returns True if deleted."""
        cursor = self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.db.commit()
        return cursor.rowcount > 0

    def delete_by_project(self, project: str) -> int:
        """Delete all memories for a project. Returns count deleted."""
        cursor = self.db.execute("DELETE FROM memories WHERE project = ?", (project,))
        self.db.commit()
        return cursor.rowcount

    def get(self, memory_id: int) -> Memory | None:
        """Get a single memory by ID."""
        row = self.db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if not row:
            return None
        return Memory(
            id=row["id"],
            content=row["content"],
            source_file=row["source_file"],
            project=row["project"],
            section=row["section"],
            content_hash=row["content_hash"],
            created_at=row["created_at"],
        )

    def list_projects(self) -> list[str]:
        """Return all distinct project names."""
        rows = self.db.execute("SELECT DISTINCT project FROM memories ORDER BY project").fetchall()
        return [row["project"] for row in rows]

    def stats(self) -> dict[str, int]:
        """Return memory counts per project."""
        rows = self.db.execute(
            "SELECT project, COUNT(*) as count FROM memories GROUP BY project ORDER BY count DESC"
        ).fetchall()
        return {row["project"]: row["count"] for row in rows}

    def count(self) -> int:
        """Total memory count."""
        return self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def close(self) -> None:
        # Checkpoint WAL to keep the file small when many processes share the DB
        try:
            self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.OperationalError:
            pass  # another connection holds a lock — skip silently
        self.db.close()
