"""SQLite + FTS5 memory store."""

import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from crossmem.stopwords import CLOSED_CLASS, partition_query

# Backward-compatible alias — callers that imported _STOP_WORDS directly still work.
_STOP_WORDS: frozenset[str] = CLOSED_CLASS


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
    scope: str = field(default="project")
    last_verified: str | None = field(default=None)
    type: str = field(default="project")
    why: str = field(default="")
    how_to_apply: str = field(default="")
    description: str = field(default="")

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
    source TEXT NOT NULL DEFAULT 'seed',
    PRIMARY KEY (canonical, term)
);

INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('auth', 'authentication', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('auth', 'credential', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('auth', 'credentials', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('auth', 'jwt', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('auth', 'token', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('auth', 'login', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('auth', 'oauth', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('auth', 'oidc', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('auth', 'sso', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('deploy', 'deployment', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('deploy', 'release', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('deploy', 'publish', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('deploy', 'ship', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('deploy', 'rollout', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('deploy', 'push', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('pypi', 'pip', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('pypi', 'registry', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('pypi', 'wheel', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('pypi', 'sdist', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('version', 'semver', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('version', 'bump', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('version', 'tag', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('version', 'changelog', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('version', 'release', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('validate', 'validation', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('validate', 'verify', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source)
    VALUES ('validate', 'verification', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('validate', 'check', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('validate', 'sanitize', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('process', 'steps', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('process', 'workflow', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('process', 'procedure', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('process', 'guide', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('process', 'howto', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('process', 'checklist', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('docker', 'container', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('docker', 'image', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('docker', 'compose', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('docker', 'kubernetes', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('docker', 'k8s', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('docker', 'pod', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('database', 'db', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('database', 'sql', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('database', 'schema', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('database', 'migration', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('database', 'orm', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('error', 'exception', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('error', 'failure', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('error', 'bug', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('error', 'crash', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('error', 'traceback', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('config', 'configuration', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('config', 'settings', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('config', 'env', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('config', 'environment', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('variables', 'vars', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('keep', 'store', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('rollback', 'roll', 'seed');
INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES ('rollback', 'revert', 'seed');
"""


class MemoryStore:
    """SQLite + FTS5 backed memory store."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path), timeout=10)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.enable_load_extension(True)
        except AttributeError:
            pass  # Python built without extension support — vec will degrade gracefully
        # WAL allows concurrent readers + one writer; upgrade silently
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass  # locked by another connection — existing mode is fine
        self.db.execute("PRAGMA busy_timeout=30000")
        self._synonym_cache: dict[str, frozenset[str]] | None = None
        self._vec_available: bool = False
        self._init_schema()
        self._try_init_vec()

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
        (3, None),  # sentinel — handled by _run_migration_3()
        (4, None),  # sentinel — handled by _run_migration_4()
        (5, None),  # sentinel — handled by _run_migration_5()
        (6, None),  # sentinel — handled by _run_migration_6()
        (7, None),  # sentinel — handled by _run_migration_7()
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
                    if version == 2:
                        self._run_migration_2()
                    elif version == 3:
                        self._run_migration_3()
                    elif version == 4:
                        self._run_migration_4()
                    elif version == 5:
                        self._run_migration_5()
                    elif version == 6:
                        self._run_migration_6()
                    elif version == 7:
                        self._run_migration_7()
                    else:
                        raise RuntimeError(f"Unsupported migration sentinel: {version}")
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

    def _run_migration_3(self) -> None:
        """Idempotent migration: add synonyms.source provenance column."""
        cols = {row[1] for row in self.db.execute("PRAGMA table_info(synonyms)").fetchall()}
        if "source" not in cols:
            self.db.executescript("""
                ALTER TABLE synonyms RENAME TO synonyms_old;
                CREATE TABLE synonyms (
                    canonical TEXT NOT NULL,
                    term TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'seed',
                    PRIMARY KEY (canonical, term)
                );
                INSERT OR IGNORE INTO synonyms (canonical, term, source)
                SELECT canonical, term, 'seed' FROM synonyms_old;
                DROP TABLE synonyms_old;
            """)

        # Re-seed as source='seed' to keep default groups present on all DBs.
        self.db.executescript(_SYNONYMS_SEED_SQL)

    def _run_migration_4(self) -> None:
        """Idempotent migration: add scope column ('project' | 'global')."""
        cols = {row[1] for row in self.db.execute("PRAGMA table_info(memories)").fetchall()}
        if "scope" not in cols:
            self.db.execute("ALTER TABLE memories ADD COLUMN scope TEXT NOT NULL DEFAULT 'project'")
            self.db.commit()

    def _run_migration_5(self) -> None:
        """Idempotent migration: add last_verified column."""
        cols = {row[1] for row in self.db.execute("PRAGMA table_info(memories)").fetchall()}
        if "last_verified" not in cols:
            self.db.execute("ALTER TABLE memories ADD COLUMN last_verified TIMESTAMP NULL")
            self.db.commit()

    def _run_migration_6(self) -> None:
        """Idempotent migration: add crossmem_config table for user-configurable settings."""
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS crossmem_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

    def _run_migration_7(self) -> None:
        """Idempotent migration: add type, why, how_to_apply, description columns."""
        cols = {row[1] for row in self.db.execute("PRAGMA table_info(memories)").fetchall()}
        for col, default in [
            ("type", "project"),
            ("why", ""),
            ("how_to_apply", ""),
            ("description", ""),
        ]:
            if col not in cols:
                self.db.execute(
                    f"ALTER TABLE memories ADD COLUMN {col} TEXT NOT NULL DEFAULT '{default}'"
                )
        self.db.commit()

    # -- Vector (embeddings) backend ---------------------------------------

    def _try_init_vec(self) -> None:
        """Try to load sqlite-vec and create the vec_memories virtual table.

        Sets self._vec_available = True only if both sqlite-vec and fastembed
        are installed. Silently degrades to FTS5-only if either is missing.
        """
        try:
            import sqlite_vec

            from crossmem import embeddings as _emb

            if not _emb.is_available():
                return
            sqlite_vec.load(self.db)
            self.db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories
                USING vec0(embedding float[384] distance_metric=cosine)
            """)
            self.db.commit()
            self._vec_available = True
            # Silently backfill a small batch of unembedded memories on startup
            self._backfill_embeddings_partial(limit=50)
        except Exception:
            self._vec_available = False

    def get_config(self, key: str, default: str = "") -> str:
        """Get a config value. Returns default if key not set."""
        row = self.db.execute("SELECT value FROM crossmem_config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_config(self, key: str, value: str) -> None:
        """Set a config value."""
        self.db.execute(
            "INSERT OR REPLACE INTO crossmem_config (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.db.commit()

    def embed_memory(self, memory_id: int) -> bool:
        """Compute and store the embedding for a memory. Returns True if stored."""
        if not self._vec_available:
            return False
        mem = self.get(memory_id)
        if not mem:
            return False
        from crossmem import embeddings

        vec = embeddings.embed(mem.content)
        if vec is None:
            return False
        self.db.execute(
            "INSERT OR REPLACE INTO vec_memories(rowid, embedding) VALUES (?, ?)",
            (memory_id, vec),
        )
        self.db.commit()
        return True

    def _backfill_embeddings_partial(self, limit: int = 50) -> int:
        """Embed up to `limit` memories that have no stored vector. Returns count embedded."""
        if not self._vec_available:
            return 0
        rows = self.db.execute(
            """SELECT m.id FROM memories m
               WHERE NOT EXISTS (SELECT 1 FROM vec_memories v WHERE v.rowid = m.id)
               LIMIT ?""",
            (limit,),
        ).fetchall()
        count = 0
        for row in rows:
            if self.embed_memory(row["id"]):
                count += 1
        return count

    def backfill_embeddings(self) -> int:
        """Embed all memories that have no stored vector. Returns count embedded."""
        if not self._vec_available:
            return 0
        rows = self.db.execute(
            """SELECT m.id FROM memories m
               WHERE NOT EXISTS (SELECT 1 FROM vec_memories v WHERE v.rowid = m.id)"""
        ).fetchall()
        count = 0
        for row in rows:
            if self.embed_memory(row["id"]):
                count += 1
        return count

    def search_vector(
        self,
        query: str,
        limit: int = 10,
        project: str | None = None,
        scope: str | None = None,
    ) -> list[SearchResult]:
        """ANN search via sqlite-vec cosine distance. Falls back to FTS5 if unavailable."""
        if not self._vec_available:
            return self.search_expanded(query, limit=limit, project=project, scope=scope)

        from crossmem import embeddings

        qvec = embeddings.embed(query)
        if qvec is None:
            return self.search_expanded(query, limit=limit, project=project, scope=scope)

        # Fetch extra candidates to allow filtering by project/scope post-KNN
        fetch_limit = limit * 4 if (project or scope) else limit
        try:
            knn_rows = self.db.execute(
                "SELECT rowid, distance FROM vec_memories WHERE embedding MATCH ? AND k = ?",
                (qvec, fetch_limit),
            ).fetchall()
        except Exception:
            return self.search_expanded(query, limit=limit, project=project, scope=scope)

        if not knn_rows:
            return []

        rowids = [r["rowid"] for r in knn_rows]
        dist_map = {r["rowid"]: r["distance"] for r in knn_rows}

        placeholders = ",".join("?" * len(rowids))
        sql = f"SELECT * FROM memories WHERE id IN ({placeholders})"
        params: list = list(rowids)
        if project:
            sql += " AND project = ?"
            params.append(project)
        if scope:
            sql += " AND scope = ?"
            params.append(scope)

        mem_rows = self.db.execute(sql, params).fetchall()
        results = []
        for row in mem_rows:
            dist = dist_map.get(row["id"], 2.0)
            # cosine_distance ∈ [0, 2]; rank = dist - 1 ∈ [-1, 1] (lower = better)
            rank = dist - 1.0
            mem = self._row_to_memory(row)
            results.append(SearchResult(memory=mem, rank=rank, highlight=mem.snippet))

        results.sort(key=lambda r: r.rank)
        return results[:limit]

    def search_hybrid(
        self,
        query: str,
        limit: int = 10,
        project: str | None = None,
        scope: str | None = None,
    ) -> list[SearchResult]:
        """Weighted combination of FTS5 and vector search (0.3 FTS + 0.7 vec).

        Falls back to vector-only if FTS returns no results, and to FTS5-only
        if the vector backend is unavailable.
        """
        if not self._vec_available:
            return self.search_expanded(query, limit=limit, project=project, scope=scope)

        fetch = limit * 2
        fts_results = self.search_expanded(query, limit=fetch, project=project, scope=scope)
        vec_results = self.search_vector(query, limit=fetch, project=project, scope=scope)

        # Build id → rank maps for both backends
        fts_map = {r.memory.id: r.rank for r in fts_results}
        vec_map = {r.memory.id: r.rank for r in vec_results}
        all_ids = set(fts_map) | set(vec_map)
        mem_by_id = {r.memory.id: r.memory for r in fts_results + vec_results}

        combined = []
        for mid in all_ids:
            fts_rank = fts_map.get(mid)
            vec_rank = vec_map.get(mid)

            # Normalize to similarity [0, 1] where 1 = best
            fts_sim = 1.0 / (1.0 + abs(fts_rank)) if fts_rank is not None else 0.0
            # vec rank ∈ [-1, 1]; cosine_sim = -vec_rank (clamped to [0,1])
            vec_sim = max(0.0, -vec_rank) if vec_rank is not None else 0.0

            hybrid_sim = 0.3 * fts_sim + 0.7 * vec_sim
            hybrid_rank = -hybrid_sim  # convert back to "lower = better"

            mem = mem_by_id[mid]
            hl = next(
                (r.highlight for r in fts_results if r.memory.id == mid),
                mem.snippet,
            )
            combined.append(SearchResult(memory=mem, rank=hybrid_rank, highlight=hl))

        combined.sort(key=lambda r: r.rank)
        return combined[:limit]

    def search_auto(
        self,
        query: str,
        limit: int = 10,
        project: str | None = None,
        scope: str | None = None,
    ) -> list[SearchResult]:
        """Dispatch to the configured search backend (fts5 | embeddings | hybrid)."""
        mode = self.get_config("search-mode", "fts5")
        if mode == "embeddings":
            return self.search_vector(query, limit=limit, project=project, scope=scope)
        if mode == "hybrid":
            return self.search_hybrid(query, limit=limit, project=project, scope=scope)
        results = self.search_expanded(query, limit=limit, project=project, scope=scope)
        # Silent fallback: FTS returned nothing but embeddings backend is available — try ANN
        if not results and self._vec_available:
            return self.search_vector(query, limit=limit, project=project, scope=scope)
        return results

    def _row_to_memory(self, row: sqlite3.Row) -> Memory:
        keys = row.keys()
        return Memory(
            id=row["id"],
            content=row["content"],
            source_file=row["source_file"],
            project=row["project"],
            section=row["section"],
            content_hash=row["content_hash"],
            created_at=row["created_at"],
            keywords=row["keywords"],
            scope=row["scope"],
            last_verified=row["last_verified"],
            type=row["type"] if "type" in keys else "project",
            why=row["why"] if "why" in keys else "",
            how_to_apply=row["how_to_apply"] if "how_to_apply" in keys else "",
            description=row["description"] if "description" in keys else "",
        )

    _VALID_TYPES: frozenset[str] = frozenset({"user", "feedback", "project", "reference"})

    def add(
        self,
        content: str,
        source_file: str,
        project: str,
        section: str = "",
        scope: str = "project",
        type: str = "project",
        why: str = "",
        how_to_apply: str = "",
        description: str = "",
    ) -> int | None:
        """Add a memory. Returns id if new, None if duplicate."""
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-empty string")
        project = project.strip() if project else ""
        if not project:
            raise ValueError("project cannot be empty")
        section = (section or "").strip()
        if scope not in ("project", "global"):
            scope = "project"
        if type not in self._VALID_TYPES:
            type = "project"
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        keywords = self._expand_keywords(content)
        try:
            cursor = self.db.execute(
                """INSERT INTO memories
                   (content, source_file, project, section, content_hash, keywords, scope,
                    last_verified, type, why, how_to_apply, description)
                   VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?)""",
                (
                    content, source_file, project, section, content_hash, keywords, scope,
                    type, why, how_to_apply, description,
                ),
            )
            self.db.commit()
            new_id = cursor.lastrowid
            if new_id is not None:
                self.embed_memory(new_id)
            return new_id
        except sqlite3.IntegrityError:
            return None

    def upsert(
        self,
        content: str,
        source_file: str,
        project: str,
        section: str = "",
        scope: str = "project",
        type: str = "project",
        why: str = "",
        how_to_apply: str = "",
        description: str = "",
    ) -> int | None:
        """Add or update a memory. Matches on (project, section, source_file).

        If a memory with the same key exists:
        - Same content → no-op, returns None
        - Different content → updates in place, returns existing id
        If no match → inserts new, returns new id
        """
        if content is None:
            raise ValueError("content cannot be None")
        section = (section or "").strip()
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
            if type not in self._VALID_TYPES:
                type = "project"
            try:
                self.db.execute(
                    """UPDATE memories
                       SET content = ?, content_hash = ?, keywords = ?,
                           last_verified = CURRENT_TIMESTAMP,
                           type = ?, why = ?, how_to_apply = ?, description = ?
                       WHERE id = ?""",
                    (
                        content, content_hash, keywords,
                        type, why, how_to_apply, description,
                        row["id"],
                    ),
                )
                self.db.commit()
                self.embed_memory(row["id"])
                return row["id"]
            except sqlite3.IntegrityError:
                # New content_hash already exists for this project — treat as no-op
                return None

        return self.add(
            content, source_file, project, section, scope,
            type, why, how_to_apply, description,
        )

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
        if not isinstance(query, str):
            return []
        limit = max(0, limit)
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
        return self._rows_to_results(rows)

    def search_expanded(
        self,
        query: str,
        limit: int = 10,
        project: str | None = None,
        scope: str | None = None,
    ) -> list[SearchResult]:
        """AND-of-ORs FTS search with synonym expansion and compound bigram handling.

        Each query token expands to an OR group containing:
        - the token itself
        - its synonyms from the synonym table
        - any adjacent bigrams (w_i + w_{i+1}) it participates in

        Bigram expansion fixes compound-word gaps: the query "roll back migration"
        adds "rollback" to both "roll" and "back" OR groups, so a memory containing
        "rollback" satisfies both groups without needing "roll" and "back" as
        separate tokens.

        Progressive fallback: when the AND query returns 0 results and the query
        has more than one token, retries each token individually and merges unique
        results up to limit. This handles vocabulary gaps where the agent uses
        different words than those in the stored memory.
        """
        if not isinstance(query, str):
            return []
        raw_tokens = re.findall(r"[a-z0-9]+", query.lower())
        corpus_size = self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        words, _ = partition_query(raw_tokens, self.db, corpus_size)
        if not words:
            return self.search(query, limit=limit, project=project)

        groups = self._get_synonym_groups()

        # Build per-index bigram sets: each adjacent pair contributes its
        # concatenation to both participating indices.
        bigrams_for: dict[int, set[str]] = {i: set() for i in range(len(words))}
        for i in range(len(words) - 1):
            bigram = words[i] + words[i + 1]
            if 4 <= len(bigram) <= 16:
                bigrams_for[i].add(bigram)
                bigrams_for[i + 1].add(bigram)

        def _build_or_group(i: int, word: str) -> str:
            synonyms = groups.get(word, frozenset())
            group = synonyms | {word} | bigrams_for[i]
            terms = [self._quote_fts_term(w) for w in sorted(group)]
            return f"({' OR '.join(terms)})" if len(terms) > 1 else terms[0]

        and_parts = [_build_or_group(i, w) for i, w in enumerate(words)]
        fts_query = " AND ".join(and_parts)

        rows = self._execute_fts(fts_query, project=project, limit=limit, scope=scope)

        # Progressive fallback: when AND returns nothing, search per token and
        # merge unique results. Only fires for multi-token queries.
        if not rows and len(words) > 1:
            seen_ids: set[int] = set()
            fallback_rows = []
            for i, word in enumerate(words):
                token_query = _build_or_group(i, word)
                fts_rows = self._execute_fts(token_query, project=project, limit=limit, scope=scope)
                for row in fts_rows:
                    if row["id"] not in seen_ids:
                        seen_ids.add(row["id"])
                        fallback_rows.append(row)
                if len(fallback_rows) >= limit:
                    break
            rows = fallback_rows[:limit]

        return self._rows_to_results(rows)

    def _execute_fts(
        self,
        fts_query: str,
        *,
        project: str | None,
        limit: int,
        scope: str | None = None,
    ) -> list[sqlite3.Row]:
        """Execute a raw FTS5 MATCH query and return raw DB rows."""
        limit = max(0, limit)
        sql = """SELECT m.*, rank, highlight(memories_fts, 0, '>>>', '<<<') as hl
                 FROM memories_fts fts
                 JOIN memories m ON m.id = fts.rowid
                 WHERE memories_fts MATCH ?"""
        params: list[str | int] = [fts_query]
        if project:
            sql += " AND m.project = ?"
            params.append(project)
        if scope:
            sql += " AND m.scope = ?"
            params.append(scope)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        return self.db.execute(sql, params).fetchall()

    def _rows_to_results(self, rows: list[sqlite3.Row]) -> list[SearchResult]:
        """Convert raw FTS DB rows to SearchResult objects."""
        return [
            SearchResult(memory=self._row_to_memory(row), rank=row["rank"], highlight=row["hl"])
            for row in rows
        ]

    @staticmethod
    def _quote_fts_term(w: str) -> str:
        """Quote terms with hyphens or underscores for FTS5 safety."""
        return f'"{w}"' if ("-" in w or "_" in w) else w

    # FTS5 operator keywords — strip from user queries to prevent query injection.
    # These would be interpreted as operators by SQLite, not as search terms.
    _FTS5_OPERATORS: frozenset[str] = frozenset({"and", "or", "not", "near"})

    @staticmethod
    def _build_fts_query(query: str, *, or_mode: bool = False) -> str:
        """Build FTS5 query string. Default to AND logic, preserve quoted phrases.

        Strips all non-word, non-hyphen characters from individual tokens to
        prevent FTS5 syntax errors (e.g. $TOKEN, user@email.com) and drops
        FTS5 operator keywords (AND/OR/NOT/NEAR) to prevent query injection.
        """
        # Extract quoted phrases first; drop blank/whitespace-only phrases
        phrases = [p for p in re.findall(r'"([^"]+)"', query) if p.strip()]
        remaining = re.sub(r'"[^"]*"', "", query).strip()

        parts = [f'"{p}"' for p in phrases]
        if remaining:
            for raw_word in remaining.split():
                # Keep only word chars and hyphens — strips $, @, #, ., etc.
                cleaned = re.sub(r"[^\w-]", "", raw_word)
                # Drop FTS5 operator keywords and empty strings
                if cleaned and cleaned.lower() not in MemoryStore._FTS5_OPERATORS:
                    parts.append(MemoryStore._quote_fts_term(cleaned))

        if not parts:
            return ""

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

    def add_synonym(self, canonical: str, term: str, source: str = "user") -> None:
        """Add a synonym pair and invalidate the cache."""
        if canonical is None or term is None:
            raise ValueError("canonical and term cannot be None")
        self.db.execute(
            "INSERT OR IGNORE INTO synonyms (canonical, term, source) VALUES (?, ?, ?)",
            (canonical.lower(), term.lower(), source.lower()),
        )
        self.db.commit()
        self._synonym_cache = None

    def remove_synonym(self, canonical: str, term: str) -> bool:
        """Remove a synonym pair. Returns True if a row was deleted."""
        if canonical is None or term is None:
            return False
        cursor = self.db.execute(
            "DELETE FROM synonyms WHERE canonical = ? AND term = ?",
            (canonical.lower(), term.lower()),
        )
        self.db.commit()
        self._synonym_cache = None
        return cursor.rowcount > 0

    def list_synonyms(self) -> dict[str, list[str]]:
        """Return all synonym groups as {canonical: [terms]}."""
        rows = self.db.execute(
            "SELECT canonical, term FROM synonyms ORDER BY canonical, term"
        ).fetchall()
        result: dict[str, list[str]] = {}
        for row in rows:
            result.setdefault(row["canonical"], []).append(row["term"])
        return result

    def list_synonyms_with_source(
        self, source: str | None = None
    ) -> dict[str, list[tuple[str, str]]]:
        """Return synonym groups with provenance, optionally filtered by source."""
        sql = "SELECT canonical, term, source FROM synonyms"
        params: list[str] = []
        if source is not None:
            sql += " WHERE source = ?"
            params.append(source.lower())
        sql += " ORDER BY canonical, term"
        rows = self.db.execute(sql, params).fetchall()
        result: dict[str, list[tuple[str, str]]] = {}
        for row in rows:
            result.setdefault(row["canonical"], []).append((row["term"], row["source"]))
        return result

    def _choose_canonical(self, a: str, b: str) -> str:
        """Choose canonical: existing canonical > higher df > lexicographic."""
        existing = {
            row[0] for row in self.db.execute("SELECT DISTINCT canonical FROM synonyms").fetchall()
        }
        a_canonical = a in existing
        b_canonical = b in existing
        if a_canonical and not b_canonical:
            return a
        if b_canonical and not a_canonical:
            return b
        a_df = self.db.execute(
            "SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?", (a,)
        ).fetchone()[0]
        b_df = self.db.execute(
            "SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?", (b,)
        ).fetchone()[0]
        if a_df != b_df:
            return a if a_df > b_df else b
        return min(a, b)

    def learn_synonyms(
        self,
        max_df_ratio: float = 0.5,
        min_df: int = 3,
        min_jaccard: float = 0.3,
    ) -> int:
        """Mine co-occurrence synonyms via Jaccard similarity. Returns new pairs added."""
        rows = self.db.execute("SELECT content FROM memories").fetchall()
        n = len(rows)
        if n == 0:
            return 0

        max_df = max(int(max_df_ratio * n), 50)

        doc_sets: dict[str, set[int]] = {}
        for i, row in enumerate(rows):
            tokens = set(re.findall(r"[a-z0-9]+", row["content"].lower())) - CLOSED_CLASS
            for token in tokens:
                doc_sets.setdefault(token, set()).add(i)

        qualified = {
            token: docs for token, docs in doc_sets.items() if min_df <= len(docs) <= max_df
        }

        existing: set[tuple[str, str]] = set()
        known_words: set[str] = set()
        for row in self.db.execute("SELECT canonical, term FROM synonyms").fetchall():
            existing.add((row["canonical"], row["term"]))
            existing.add((row["term"], row["canonical"]))
            known_words.add(row["canonical"])
            known_words.add(row["term"])

        tokens_list = sorted(qualified.keys())
        added = 0
        for i, a in enumerate(tokens_list):
            if a in known_words:
                continue
            docs_a = qualified[a]
            for b in tokens_list[i + 1 :]:
                if b in known_words:
                    continue
                docs_b = qualified[b]
                intersection = len(docs_a & docs_b)
                if intersection == 0:
                    continue
                jaccard = intersection / len(docs_a | docs_b)
                if jaccard < min_jaccard:
                    continue
                if (a, b) in existing or (b, a) in existing:
                    continue
                canonical = self._choose_canonical(a, b)
                term = b if canonical == a else a
                self.add_synonym(canonical, term, source="learned")
                existing.add((canonical, term))
                existing.add((term, canonical))
                added += 1

        return added

    def get_all_for_backfill(self) -> list[Memory]:
        """Return all memories where keywords column is empty."""
        rows = self.db.execute("SELECT * FROM memories WHERE keywords = '' ORDER BY id").fetchall()
        return [self._row_to_memory(row) for row in rows]

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
        limit = max(0, limit)
        rows = self.db.execute(
            "SELECT * FROM memories WHERE project = ? ORDER BY created_at DESC LIMIT ?",
            (project, limit),
        ).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def get_shared_sections(self, project: str, limit: int = 20) -> list[Memory]:
        """Get memories from other projects that share section names with this project."""
        limit = max(0, limit)
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
        return [self._row_to_memory(row) for row in rows]

    def update(
        self,
        memory_id: int,
        content: str,
        section: str | None = None,
        project: str | None = None,
        scope: str | None = None,
    ) -> bool:
        """Update a memory in place. Returns True if updated."""
        if not isinstance(content, str) or not content.strip():
            return False
        if scope is not None and scope not in ("project", "global"):
            return False

        mem = self.get(memory_id)
        if not mem:
            return False

        # Normalize: strip whitespace; treat empty string as "keep existing" for project,
        # strip for section (empty section = root-level, which is valid).
        normalized_project = project.strip() if project is not None else None
        new_project = normalized_project if normalized_project else mem.project
        new_section = section.strip() if section is not None else mem.section
        new_scope = scope if scope is not None else mem.scope
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        keywords = self._expand_keywords(content)

        self.db.execute(
            """UPDATE memories
               SET content = ?, section = ?, project = ?, content_hash = ?, keywords = ?, scope = ?,
                   last_verified = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (content, new_section, new_project, content_hash, keywords, new_scope, memory_id),
        )
        self.db.commit()
        self.embed_memory(memory_id)
        return True

    def verify(self, memory_id: int) -> bool:
        """Mark a memory as verified today without changing content. Returns True if updated."""
        cursor = self.db.execute(
            "UPDATE memories SET last_verified = CURRENT_TIMESTAMP WHERE id = ?",
            (memory_id,),
        )
        self.db.commit()
        return cursor.rowcount > 0

    def delete(self, memory_id: int) -> bool:
        """Delete a memory by ID. Returns True if deleted."""
        cursor = self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        if cursor.rowcount > 0 and self._vec_available:
            self.db.execute("DELETE FROM vec_memories WHERE rowid = ?", (memory_id,))
        self.db.commit()
        return cursor.rowcount > 0

    def delete_by_project(self, project: str) -> int:
        """Delete all memories for a project. Returns count deleted."""
        cursor = self.db.execute("DELETE FROM memories WHERE project = ?", (project,))
        self.db.commit()
        return cursor.rowcount

    def purge_stale(self) -> int:
        """Delete memories whose source_file no longer exists on disk. Returns count deleted."""
        import os

        rows = self.db.execute(
            "SELECT id, source_file FROM memories WHERE source_file IS NOT NULL"
        ).fetchall()
        stale_ids = [row["id"] for row in rows if not os.path.exists(row["source_file"])]
        if not stale_ids:
            return 0
        placeholders = ",".join("?" * len(stale_ids))
        self.db.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", stale_ids)
        if self._vec_available:
            self.db.execute(f"DELETE FROM vec_memories WHERE rowid IN ({placeholders})", stale_ids)
        self.db.commit()
        return len(stale_ids)

    def get(self, memory_id: int) -> Memory | None:
        """Get a single memory by ID."""
        row = self.db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if not row:
            return None
        return self._row_to_memory(row)

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

    def get_global_memories(self, query: str | None = None, limit: int = 20) -> list[Memory]:
        """Return globally scoped memories, optionally ranked by FTS relevance.

        Deduplicates by content_hash so auto-promoted patterns that were saved
        in multiple projects appear only once. Uses synonym-expanded search when
        a query is provided for consistent recall behaviour with mem_recall.
        """
        limit = max(0, limit)
        if query:
            results = self.search_expanded(query, limit=limit, scope="global")
            seen_hashes: set[str] = set()
            deduped: list[Memory] = []
            for r in results:
                if r.memory.content_hash not in seen_hashes:
                    seen_hashes.add(r.memory.content_hash)
                    deduped.append(r.memory)
            return deduped
        rows = self.db.execute(
            """SELECT * FROM memories
               WHERE id IN (
                   SELECT MIN(id) FROM memories WHERE scope = 'global' GROUP BY content_hash
               )
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def auto_promote_patterns(self, min_projects: int = 2) -> int:
        """Promote mem_save memories saved identically in 2+ projects to global scope.

        Only promotes source_file='mcp:mem_save' rows — ingested doc "duplicates"
        across projects are path-encoding artifacts, not genuine patterns.
        Returns the count of memories promoted.
        """
        min_projects = max(1, min_projects)
        rows = self.db.execute(
            """SELECT content_hash
               FROM memories
               WHERE source_file = 'mcp:mem_save' AND scope = 'project'
               GROUP BY content_hash
               HAVING COUNT(DISTINCT project) >= ?""",
            (min_projects,),
        ).fetchall()

        if not rows:
            return 0

        hashes = [row["content_hash"] for row in rows]
        placeholders = ",".join("?" * len(hashes))
        cursor = self.db.execute(
            f"UPDATE memories SET scope = 'global'"
            f" WHERE content_hash IN ({placeholders}) AND source_file = 'mcp:mem_save'",
            hashes,
        )
        if cursor.rowcount:
            self.db.commit()
        return cursor.rowcount

    def set_scope(self, memory_id: int, scope: str) -> bool:
        """Explicitly set the scope of a memory. Returns True if updated."""
        if scope not in ("project", "global"):
            return False
        cursor = self.db.execute("UPDATE memories SET scope = ? WHERE id = ?", (scope, memory_id))
        self.db.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        # Checkpoint WAL to keep the file small when many processes share the DB
        try:
            self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.OperationalError:
            pass  # another connection holds a lock — skip silently
        self.db.close()
