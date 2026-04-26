# Changelog

All notable changes to crossmem are documented here.

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
