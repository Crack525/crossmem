# Changelog

All notable changes to crossmem are documented here.

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
