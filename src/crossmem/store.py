"""SQLite + FTS5 memory store."""

import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "it", "its", "this", "that", "these", "those",
    "i", "we", "you", "he", "she", "they", "my", "our", "your", "their",
})


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
    keywords: str = field(default="")

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

_SYNONYMS_SEED_SQL = """
CREATE TABLE IF NOT EXISTS synonyms (
    canonical TEXT NOT NULL,
    term TEXT NOT NULL,
    PRIMARY KEY (canonical, term)
);

INSERT OR IGNORE INTO synonyms VALUES ('auth', 'authentication');
INSERT OR IGNORE INTO synonyms VALUES ('auth', 'credential');
INSERT OR IGNORE INTO synonyms VALUES ('auth', 'credentials');
INSERT OR IGNORE INTO synonyms VALUES ('auth', 'jwt');
INSERT OR IGNORE INTO synonyms VALUES ('auth', 'token');
INSERT OR IGNORE INTO synonyms VALUES ('auth', 'login');
INSERT OR IGNORE INTO synonyms VALUES ('auth', 'oauth');
INSERT OR IGNORE INTO synonyms VALUES ('auth', 'oidc');
INSERT OR IGNORE INTO synonyms VALUES ('auth', 'sso');
INSERT OR IGNORE INTO synonyms VALUES ('deploy', 'deployment');
INSERT OR IGNORE INTO synonyms VALUES ('deploy', 'release');
INSERT OR IGNORE INTO synonyms VALUES ('deploy', 'publish');
INSERT OR IGNORE INTO synonyms VALUES ('deploy', 'ship');
INSERT OR IGNORE INTO synonyms VALUES ('deploy', 'rollout');
INSERT OR IGNORE INTO synonyms VALUES ('deploy', 'push');
INSERT OR IGNORE INTO synonyms VALUES ('pypi', 'pip');
INSERT OR IGNORE INTO synonyms VALUES ('pypi', 'registry');
INSERT OR IGNORE INTO synonyms VALUES ('pypi', 'wheel');
INSERT OR IGNORE INTO synonyms VALUES ('pypi', 'sdist');
INSERT OR IGNORE INTO synonyms VALUES ('version', 'semver');
INSERT OR IGNORE INTO synonyms VALUES ('version', 'bump');
INSERT OR IGNORE INTO synonyms VALUES ('version', 'tag');
INSERT OR IGNORE INTO synonyms VALUES ('version', 'changelog');
INSERT OR IGNORE INTO synonyms VALUES ('version', 'release');
INSERT OR IGNORE INTO synonyms VALUES ('validate', 'validation');
INSERT OR IGNORE INTO synonyms VALUES ('validate', 'verify');
INSERT OR IGNORE INTO synonyms VALUES ('validate', 'verification');
INSERT OR IGNORE INTO synonyms VALUES ('validate', 'check');
INSERT OR IGNORE INTO synonyms VALUES ('validate', 'sanitize');
INSERT OR IGNORE INTO synonyms VALUES ('process', 'steps');
INSERT OR IGNORE INTO synonyms VALUES ('process', 'workflow');
INSERT OR IGNORE INTO synonyms VALUES ('process', 'procedure');
INSERT OR IGNORE INTO synonyms VALUES ('process', 'guide');
INSERT OR IGNORE INTO synonyms VALUES ('process', 'howto');
INSERT OR IGNORE INTO synonyms VALUES ('docker', 'container');
INSERT OR IGNORE INTO synonyms VALUES ('docker', 'image');
INSERT OR IGNORE INTO synonyms VALUES ('docker', 'compose');
INSERT OR IGNORE INTO synonyms VALUES ('docker', 'kubernetes');
INSERT OR IGNORE INTO synonyms VALUES ('docker', 'k8s');
INSERT OR IGNORE INTO synonyms VALUES ('docker', 'pod');
INSERT OR IGNORE INTO synonyms VALUES ('database', 'db');
INSERT OR IGNORE INTO synonyms VALUES ('database', 'sql');
INSERT OR IGNORE INTO synonyms VALUES ('database', 'schema');
INSERT OR IGNORE INTO synonyms VALUES ('database', 'migration');
INSERT OR IGNORE INTO synonyms VALUES ('database', 'orm');
INSERT OR IGNORE INTO synonyms VALUES ('error', 'exception');
INSERT OR IGNORE INTO synonyms VALUES ('error', 'failure');
INSERT OR IGNORE INTO synonyms VALUES ('error', 'bug');
INSERT OR IGNORE INTO synonyms VALUES ('error', 'crash');
INSERT OR IGNORE INTO synonyms VALUES ('error', 'traceback');
INSERT OR IGNORE INTO synonyms VALUES ('config', 'configuration');
INSERT OR IGNORE INTO synonyms VALUES ('config', 'settings');
INSERT OR IGNORE INTO synonyms VALUES ('config', 'env');
INSERT OR IGNORE INTO synonyms VALUES ('config', 'environment');
"""


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
        self._synonym_cache: dict[str, frozenset[str]] | None = None
        self._init_schema()

    # -- Schema migrations ------------------------------------------------
    # Each entry is (version, sql).  Versions are applied in order.
    # Existing databases that pre-date the migration table are treated as
    # version 0 (the original schema already exists; migration 1 is a no-op
    # for them because every statement uses IF NOT EXISTS).
    # sql=None is a sentinel: _init_schema calls the corresponding Python
    # migration method instead of executescript().
    _MIGRATIONS: list[tuple[int, str | None]] = [
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
        (2, None),  # sentinel — handled by _run_migration_2()
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
                if sql is None:
                    self._run_migration_2()
                else:
                    self.db.executescript(sql)
                self.db.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (version,),
                )
                self.db.commit()

    def _run_migration_2(self) -> None:
        """Idempotent stepwise migration: keywords column + synonyms table + FTS rebuild."""
        # Step 1: Add keywords column only if absent
        cols = {row[1] for row in self.db.execute("PRAGMA table_info(memories)").fetchall()}
        if "keywords" not in cols:
            self.db.execute("ALTER TABLE memories ADD COLUMN keywords TEXT NOT NULL DEFAULT ''")

        # Step 2: Rebuild FTS if keywords column absent OR row counts diverge
        fts_cols = {row[1] for row in self.db.execute("PRAGMA table_info(memories_fts)").fetchall()}
        fts_row_count = self.db.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
        mem_row_count = self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        needs_rebuild = "keywords" not in fts_cols or fts_row_count != mem_row_count
        if needs_rebuild:
            self.db.executescript("""
                DROP TABLE IF EXISTS memories_fts;
                CREATE VIRTUAL TABLE memories_fts USING fts5(
                    content, project, section, keywords,
                    content='memories', content_rowid='id',
                    tokenize='porter unicode61'
                );
                INSERT INTO memories_fts(rowid, content, project, section, keywords)
                SELECT id, content, project, section, keywords FROM memories;
            """)

        # Step 3: Recreate triggers for 4-column schema (DROP IF EXISTS is safe)
        self.db.executescript("""
            DROP TRIGGER IF EXISTS memories_ai;
            DROP TRIGGER IF EXISTS memories_ad;
            DROP TRIGGER IF EXISTS memories_au;
            CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content, project, section, keywords)
                VALUES (new.id, new.content, new.project, new.section, new.keywords);
            END;
            CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, project, section, keywords)
                VALUES ('delete', old.id, old.content, old.project, old.section, old.keywords);
            END;
            CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, project, section, keywords)
                VALUES ('delete', old.id, old.content, old.project, old.section, old.keywords);
                INSERT INTO memories_fts(rowid, content, project, section, keywords)
                VALUES (new.id, new.content, new.project, new.section, new.keywords);
            END;
        """)

        # Step 4: Create synonyms table and seed data
        self.db.executescript(_SYNONYMS_SEED_SQL)

    def add(
        self,
        content: str,
        source_file: str,
        project: str,
        section: str = "",
    ) -> int | None:
        """Add a memory. Returns id if new, None if duplicate."""
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        keywords = self._expand_keywords(content)
        try:
            cursor = self.db.execute(
                """INSERT INTO memories (content, source_file, project, section, content_hash, keywords)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (content, source_file, project, section, content_hash, keywords),
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
            keywords = self._expand_keywords(content)
            try:
                self.db.execute(
                    """UPDATE memories
                       SET content = ?, content_hash = ?, keywords = ?
                       WHERE id = ?""",
                    (content, content_hash, keywords, row["id"]),
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
        """Full-text search across all memories (exact AND, column-scoped).

        Query logic:
        - Multiple words default to AND (all terms must match)
        - Quoted phrases are preserved as exact matches
        - Single words search as-is
        - or_mode=True uses OR logic (any term can match) — useful for fuzzy/prompt searches
        - Only searches content/project/section columns — keywords column excluded
        """
        fts_query = self._build_fts_query(query, or_mode=or_mode)
        if not fts_query:
            return []

        # Parentheses required: without them, OR terms escape the column scope
        scoped_query = f"{{content project section}} : ({fts_query})"

        sql = """SELECT m.*, rank, highlight(memories_fts, 0, '>>>', '<<<') as hl
                 FROM memories_fts fts
                 JOIN memories m ON m.id = fts.rowid
                 WHERE memories_fts MATCH ?"""
        params: list[str | int] = [scoped_query]

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
                    keywords=row["keywords"],
                ),
                rank=row["rank"],
                highlight=row["hl"],
            )
            for row in rows
        ]

    def search_expanded(
        self,
        query: str,
        limit: int = 10,
        project: str | None = None,
    ) -> list[SearchResult]:
        """AND-of-ORs FTS search with synonym expansion across all 4 FTS columns.

        Each query word expands to an OR group (word + synonyms); all groups AND'd.
        Falls back to exact AND when no synonyms defined for query words.
        Searches content, project, section, AND keywords columns.
        """
        words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in _STOP_WORDS]
        if not words:
            return self.search(query, limit=limit, project=project)

        groups = self._get_synonym_groups()

        and_parts = []
        for word in words:
            synonyms = groups.get(word, frozenset())
            group = synonyms | {word}
            terms = [self._quote_fts_term(w) for w in sorted(group)]
            and_parts.append(f"({' OR '.join(terms)})" if len(terms) > 1 else terms[0])

        fts_query = " AND ".join(and_parts)

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
                    keywords=row["keywords"],
                ),
                rank=row["rank"],
                highlight=row["hl"],
            )
            for row in rows
        ]

    @staticmethod
    def _quote_fts_term(w: str) -> str:
        """Quote terms with hyphens or underscores for FTS5 safety."""
        return f'"{w}"' if ("-" in w or "_" in w) else w

    @staticmethod
    def _build_fts_query(query: str, *, or_mode: bool = False) -> str:
        """Build FTS5 query string. Default to AND logic, preserve quoted phrases."""
        # Extract quoted phrases first
        phrases = re.findall(r'"([^"]+)"', query)
        remaining = re.sub(r'"[^"]*"', "", query).strip()

        parts = [f'"{p}"' for p in phrases]
        if remaining:
            words = remaining.split()
            # Quote hyphenated or underscored words — FTS5 interprets "-" as column
            # filter and "_" as token separator (unicode61 tokenizer), both cause
            # incorrect query expansion or wrong token splitting.
            parts.extend(MemoryStore._quote_fts_term(w) for w in words)

        if not parts:
            return query

        joiner = " OR " if or_mode else " AND "
        return joiner.join(parts)

    def _get_synonym_groups(self) -> dict[str, frozenset[str]]:
        """Bidirectional synonym map: term → frozenset of all synonyms across all groups."""
        if self._synonym_cache is not None:
            return self._synonym_cache

        rows = self.db.execute("SELECT canonical, term FROM synonyms").fetchall()

        canonical_to_terms: dict[str, set[str]] = {}
        for row in rows:
            canonical_to_terms.setdefault(row["canonical"], set()).add(row["term"])

        # Build bidirectional map — each word maps to all others in its group.
        # A word in multiple groups gets the UNION of those groups' synonyms (intentional).
        cache: dict[str, set[str]] = {}
        for canonical, terms in canonical_to_terms.items():
            full_group = terms | {canonical}
            for word in full_group:
                cache.setdefault(word, set()).update(full_group - {word})

        self._synonym_cache = {k: frozenset(v) for k, v in cache.items()}
        return self._synonym_cache

    def _expand_keywords(self, text: str) -> str:
        """Return synonym expansion delta — terms not already in text, for the keywords column."""
        words_in_text = set(re.findall(r"[a-z0-9]+", text.lower()))
        groups = self._get_synonym_groups()
        expansion: set[str] = set()
        for word in words_in_text:
            for synonym in groups.get(word, frozenset()):
                if synonym not in words_in_text:
                    expansion.add(synonym)
        return " ".join(sorted(expansion))

    def add_synonym(self, canonical: str, term: str) -> None:
        """Add a synonym pair and invalidate the cache."""
        self.db.execute(
            "INSERT OR IGNORE INTO synonyms VALUES (?, ?)",
            (canonical.lower(), term.lower()),
        )
        self.db.commit()
        self._synonym_cache = None

    def list_synonyms(self) -> dict[str, list[str]]:
        """Return all synonym groups as {canonical: [terms]}."""
        rows = self.db.execute(
            "SELECT canonical, term FROM synonyms ORDER BY canonical, term"
        ).fetchall()
        result: dict[str, list[str]] = {}
        for row in rows:
            result.setdefault(row["canonical"], []).append(row["term"])
        return result

    def get_all_for_backfill(self) -> list[Memory]:
        """Return all memories where keywords column is empty."""
        rows = self.db.execute(
            "SELECT * FROM memories WHERE keywords = '' ORDER BY id"
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
                keywords=row["keywords"],
            )
            for row in rows
        ]

    def backfill_keywords(self) -> int:
        """Expand keywords for all memories with empty keywords column. Returns count updated."""
        memories = self.get_all_for_backfill()
        count = 0
        for mem in memories:
            kw = self._expand_keywords(mem.content)
            if kw:
                self.db.execute(
                    "UPDATE memories SET keywords = ? WHERE id = ?",
                    (kw, mem.id),
                )
                count += 1
        if count:
            self.db.commit()
        return count

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
                keywords=row["keywords"],
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
                keywords=row["keywords"],
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
        keywords = self._expand_keywords(content)

        self.db.execute(
            """UPDATE memories
               SET content = ?, section = ?, project = ?, content_hash = ?, keywords = ?
               WHERE id = ?""",
            (content, new_section, new_project, content_hash, keywords, memory_id),
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
            keywords=row["keywords"],
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
