# Changelog

All notable changes to crossmem are documented here.

## [1.8.0] — 2026-05-03

### Added

**File-backed memory — single source of truth**

`mem_save` now writes durable `.md` files to `~/.crossmem/memories/<project>/<content_hash8>.md` alongside each DB row. These files are the source of truth: they survive a DB wipe and are re-ingested at startup via `ingest_crossmem_saved()`.

- **`_write_backing_file()`**: writes YAML frontmatter (name, description, type, scope, why, how_to_apply) + body content
- **`ingest_crossmem_saved()`**: new ingest function in `ingest.py`; reads `~/.crossmem/memories/` on startup, parses frontmatter, upserts rows — same pattern as `ingest_claude_memory` but treats each file as a single memory (no section splitting)
- **`get_store()` auto-ingest**: now calls `ingest_crossmem_saved(store)` alongside existing ingest calls on every DB open
- **`mem_forget()` cleanup**: deletes backing file from disk after DB delete when source_file points inside `~/.crossmem/memories/`
- **`mem_update()` sync**: overwrites backing file with updated content/section/scope after successful DB update
- **Dedup preserved**: content hash is computed before `store.add()` to derive the backing path; file is only written if add succeeds (no orphans on near-dup rejection)

Eliminates file/DB drift — one place to update, files re-hydrate the DB on a clean install.

---

## [1.7.3] — 2026-05-03

### Fixed

- **SQLite extension loading**: `enable_load_extension(True)` was missing from `MemoryStore.__init__()`, causing `sqlite_vec.load()` to raise `OperationalError: not authorized` silently. `_vec_available` was always `False` even when `fastembed` and `sqlite-vec` were installed — the v1.7.2 FTS5→embeddings fallback was dormant. Extension now loads correctly.

---

## [1.7.0] — 2026-05-03

### Added

**Semantic search via embeddings backend (optional)**

FTS5 keyword search is now supplemented by a full vector search backend using fastembed + sqlite-vec. Install with `pip install 'crossmem[embeddings]'` to enable.

- **`embeddings.py`**: Lazy-loaded fastembed singleton; embeds text to 384-dim float32 vectors using `sentence-transformers/all-MiniLM-L6-v2` (~22MB ONNX model, no torch required)
- **`vec_memories` virtual table**: `vec0` table storing per-memory embeddings; created on first use when embeddings backend is available
- **`search_vector()`**: ANN cosine search returning `rank = distance - 1 ∈ [-1, 1]`; threshold `PROMPT_SEARCH_MIN_RANK_VECTOR = -0.5` (cosine similarity ≥ 0.5)
- **`search_hybrid()`**: Combines FTS5 + vector scores — `0.3 * fts_sim + 0.7 * vec_sim` — for best of both keyword precision and semantic recall
- **`search_auto()`**: Dispatches to `search_expanded` / `search_vector` / `search_hybrid` based on configured search mode
- **Migration 6**: Adds `crossmem_config` table (key/value) for user-configurable settings
- **`crossmem config set/get`**: CLI subcommands to read/write configuration; `crossmem config set search-mode hybrid`
- **`crossmem config backfill-embeddings`**: Embeds all existing memories that lack a stored vector
- **Graceful degradation**: `_vec_available` flag ensures clean fallback to FTS5 if sqlite-vec or fastembed not installed; zero breaking changes for existing users

**To upgrade to semantic search:**
```
pip install 'crossmem[embeddings]'
crossmem config set search-mode hybrid
crossmem config backfill-embeddings
```

---

## [1.6.0] — 2026-05-03

### Changed

**Fill-forward budget allocation — full memory content injected**

- `_build_recall_output` (SessionStart recall) and `prompt_search` (UserPromptSubmit) both previously used `mem.snippet` — a hard 200-char cap — leaving ~2900 of the 4000-char budget unused per typical invocation
- Both paths now use `mem.content.strip()` with fill-forward allocation: each memory gets up to the remaining budget, truncated at the last sentence boundary within that space (falling back to word boundary, then hard cut with `…`)
- In practice: 5 memories with ~800-char bodies now consume ~3800 chars instead of ~1100, giving the LLM 3× more context per injection
- Injection log snippet increased from 200 → 500 chars for better hit-rate keyword coverage in `tokenxray --memory-impact`
- `snippet` property in `store.py` unchanged — still 200 chars for display in `crossmem list` and CLI output

---

## [1.5.0] — 2026-05-03

### Added

**Memory injection logging for hit-rate analysis**

crossmem now logs every memory injection event so tokenxray can measure which memories the LLM actually uses.

- Every `prompt-search` invocation that passes the relevance gate appends one JSON record to `~/.tokenxray/memory_injections.jsonl` — timestamp, cwd, project, and list of injected memory IDs + snippets
- Enables `tokenxray --memory-impact` to correlate injected memories against subsequent assistant responses and report per-memory hit rates
- Zero overhead when no matches pass the rank gate; write errors are silently ignored to never interrupt the hook

---

## [1.4.0] — 2026-05-03

### Added

**Agent UX: relevance gate + stale detection**

Two improvements that make crossmem memories more trustworthy as agent context.

- **`prompt-search` relevance gate** — FTS5 BM25 rank threshold (`PROMPT_SEARCH_MIN_RANK = -0.1`) filters out weak matches before injecting memories into prompt context. With a realistic DB, strong matches score -2 to -5; near-zero scores are noise and now suppressed.
- **Stale detection in `mem_recall`** — every recalled memory now runs `_stale_check`: if `source_file` no longer exists, or if file paths referenced in the memory content (e.g. `src/foo.py`, `tests/bar.py`) are not found on disk, the memory is labelled `[stale: <reason>]` instead of showing a freshness date. Agents can immediately see which memories have rotted and decide to update or discard them.

---

## [1.3.0] — 2026-05-03

### Added

**Memory freshness tracking**

Agents were silently trusting stale memories — there was no signal to distinguish "written 3 months ago, never re-verified" from "confirmed accurate yesterday." v1.3.0 adds a `last_verified` timestamp to every memory.

- `last_verified` is set automatically on every `mem_save`, `mem_update`, and ingest write
- New `mem_verify(memory_id)` MCP tool stamps a memory as verified today without changing its content — use it when you've confirmed a memory is still accurate but nothing needs to change
- `mem_recall` and `mem_search` now show `[verified: YYYY-MM-DD]` or `[unverified]` next to every memory, so agents can judge trust level at a glance
- Schema migration 5 (idempotent `ALTER TABLE`) — runs automatically on first startup; existing memories get `[unverified]` until re-confirmed

---


## [1.2.0] — 2026-04-28

### Added

- **Scope model** — memories now carry `scope='project'|'global'`. Project-scoped memories are returned only in their project's recall; global memories surface across all projects.
- `auto_promote_patterns()` — promotes memories saved identically across 2+ projects to global scope automatically. Runs as part of `mem_recall` and `crossmem recall`.
- `store.upsert()` — idempotent write: matches on `(project, section, source_file)`; updates content in place when changed, no-ops when identical. Used by `mem_ingest` and `ingest_project_docs` to safely re-run without duplicate rows.

### Improved

- **Project name accuracy** — `extract_project_name()` now strips the home-directory prefix before walking path segments, eliminating false project names like `"documents"` or `"personal"`.
- `resolve_project()` now uses hyphen-boundary suffix matching, preventing `"my-app"` from matching a project named `"app"`.

### Hardened

- `store.add()` / `store.upsert()` / `store.update()` — reject non-string, `None`, and whitespace-only content with a clear `ValueError` instead of propagating downstream errors.
- `store.search()` / `store.search_expanded()` — return `[]` immediately on non-string query (was `TypeError` in `re.findall`).
- `_build_fts_query()` — blank quoted phrases are filtered before the FTS5 query is issued (prevented `'"  "'` from matching everything).
- `auto_promote_patterns(min_projects=0)` logic bomb patched — `min_projects` is clamped to ≥ 1 so a zero argument never promotes all single-project memories to global.
- `server.resolve_project()` — type-guards `cwd` before calling `Path(cwd)`.
- `ingest_project_docs(project="")` — empty-string project now falls back to `derive_project_name()`.

---

## [1.1.0] — 2026-04-26

### Improved

**Smarter search — two-layer token noise filter**

crossmem's search now uses a linguistically grounded noise filter instead of ad-hoc stop word lists.

Layer 1 filters 168 closed-class English words (articles, prepositions, pronouns, conjunctions, auxiliaries, determiners) — word classes that are linguistically fixed and carry no searchable meaning.

Layer 2 applies corpus-adaptive IDF: any token appearing in more than 40% of documents is treated as project-specific noise. Computed via `FTS5 MATCH` count at query time, which handles porter stemming automatically. Activates at corpus size ≥ 50 to prevent false positives on small collections.

**Concrete improvement**: the query `"set up auth"` previously returned logging noise because `"up"` was not filtered. It is now correctly identified as a preposition and excluded, leaving only `"auth"` as the search token.

**Single source of truth**: three diverging stop word lists across `store.py`, `graph.py`, and `hooks.py` are replaced by `stopwords.py`. All search, synonym mining, and knowledge graph code paths now use the same vocabulary.

### Added

- `src/crossmem/stopwords.py` — new module exporting `CLOSED_CLASS` (168 words), `CONVERSATIONAL_FILLER` (35 words), `is_noise_token()`, and `partition_query()`
- `tests/test_stopwords.py` — 28 tests covering both layers, edge cases, and corpus-size gating

### Removed

- Inline stop word sets in `store.py` (53 words), `graph.py` (80 words), and `hooks.py` (130 lines) — all replaced by imports from `stopwords.py`

---

## [1.0.0] — 2026-04-06

Initial release.

- Local SQLite + FTS5 memory store for Claude Code, GitHub Copilot, and Gemini CLI
- MCP server with `mem_recall`, `mem_search`, `mem_save`, `mem_update`, `mem_forget`, `mem_get`, `mem_init`, `mem_ingest`
- Hook installation for Claude Code (SessionStart + UserPromptSubmit), GitHub Copilot (workspace + global), VS Code agent mode, and Gemini CLI
- Tiered recall: curated memories > tool memories > project docs
- Mid-session recall: every prompt searched against memories before model responds
- Synonym learning for technical vocabulary
- Knowledge graph visualization
- `crossmem setup` one-command onboarding
- `crossmem doctor` health check
