# crossmem

[![PyPI](https://img.shields.io/pypi/v/crossmem)](https://pypi.org/project/crossmem/)
[![Python](https://img.shields.io/pypi/pyversions/crossmem)](https://pypi.org/project/crossmem/)
[![Downloads](https://img.shields.io/pypi/dm/crossmem)](https://pypistats.org/packages/crossmem)
[![License](https://img.shields.io/pypi/l/crossmem)](https://github.com/Crack525/crossmem/blob/main/LICENSE)

One search across all your Claude Code, GitHub Copilot, and Gemini CLI memories — every project, every tool.

![Before and after crossmem](visuals/problem-solution.png)

![crossmem demo](demo/demo.gif)

## The problem

You use AI coding assistants across multiple projects. Each project's memories are locked in a silo — and each tool has its own silo too. You solved credential masking in your backend API three months ago, but when you need it in a new microservice, your AI assistant starts from scratch.

```
~/.claude/projects/
├── backend-api/memory/MEMORY.md    ← Claude remembers here
├── mobile-app/memory/MEMORY.md    ← ...but can't see here
└── data-pipeline/memory/MEMORY.md ← ...or here

~/.gemini/GEMINI.md                ← Gemini's memories (separate silo entirely)
```

Every project is a silo. Every tool is a silo. Knowledge doesn't compound — it resets.

## The fix

```bash
pip install crossmem
crossmem setup     # one-time: hooks + instructions + ingest
```

That's it. Every Claude Code, Copilot, and Gemini session now starts with cross-project context — automatically. No per-project setup. New projects auto-initialize on first access.

### What happens under the hood

```
cd ~/any-project
claude                    # hook fires → auto-ingest → auto-init → recall
```

1. **Auto-ingest** — pulls new memories from Claude, Copilot, and Gemini native files
2. **Auto-init** — first time in a project? Indexes README.md, CLAUDE.md, etc.
3. **Tiered recall** — returns the most relevant context within a token budget:
   curated memories → tool memories → CLAUDE.md → CONTRIBUTING.md → README.md

Works on `startup`, `resume`, and `compact` — long sessions stay fresh.

### How crossmem differs

- **vs Mem0** — Mem0 is cloud-based and requires an API key. crossmem is **local-only** with zero accounts.
- **vs Basic Memory** — Basic Memory works within one tool. crossmem aggregates **across tools and projects**.
- **vs grep** — crossmem parses multiple formats, deduplicates, and runs as an MCP server — your AI assistant queries it automatically.

## Quick start

```bash
pip install crossmem      # 1. Install (or: uv pip install crossmem)
crossmem setup            # 2. One command: hooks + instructions + ingest
```

Done. Every AI coding session now has cross-project memory.

`setup` runs three things:
- **install-hook** — Claude Code SessionStart hook (fires on startup, resume, compact)
- **install-instructions** — Copilot + Gemini config files (prompts `mem_recall()` at session start and after compaction)
- **ingest** — pulls existing memories from all tools into the crossmem DB

To also enable MCP tools (search, save, update), add the MCP server to your config (see [MCP Server](#mcp-server) below).

## Usage

```bash
# One-time setup (hook + instructions + ingest)
crossmem setup

# Recall memories for current project (runs automatically via hook)
crossmem recall                  # auto-detects project from cwd
crossmem recall -p backend-api   # explicit project

# Search across every project
crossmem search "JWT token rotation"
crossmem search "retry strategy" -p backend-api
crossmem search "docker compose" -n 5

# Save a discovery
crossmem save "Always use middleware for credential masking" -p backend-api -s Patterns

# Update a memory in place (preserves ID)
crossmem update 42 "corrected content here"
crossmem update 42 "moved content" -s Experiments  # change section too

# Delete stale or wrong memories
crossmem forget 42                   # delete memory #42 (with confirmation)
crossmem forget -p old-app           # delete all memories for a project
crossmem forget 42 --confirm         # skip confirmation prompt

# Index project documentation manually (usually auto-detected)
crossmem init                        # current directory
crossmem init -p my-api              # explicit project name
crossmem init --path ~/projects/api  # different directory

# Re-ingest tool memories manually
crossmem ingest

# Auto-load memories at Claude Code session start
crossmem install-hook            # one-time setup (included in setup)
crossmem install-hook --uninstall  # remove the hook

# Add mem_recall instruction to Copilot + Gemini configs
crossmem install-instructions        # per-project for Copilot, global for Gemini
crossmem install-instructions --uninstall

# Visualize the knowledge graph
crossmem graph

# See what's in the database
crossmem stats
```

### Legacy commands

These still work but are deprecated — MCP + auto-ingest handle this automatically now.

```bash
crossmem sync                        # one-way Claude → Gemini file sync
crossmem sync -p backend-api        # sync one project
crossmem sync-watch                  # polls every 30s
```

## How it works

1. **Auto-ingest** — Every recall pulls the latest memories from Claude Code, Copilot, and Gemini CLI native files
2. **Auto-init** — First access to a new project? crossmem finds README.md, CLAUDE.md, etc. and indexes them automatically
3. **Index** — Stores everything locally in SQLite — no cloud, no API keys, no accounts
4. **Tiered recall** — Returns the most relevant context within a token budget: curated > tool memories > CLAUDE.md > CONTRIBUTING.md > README.md
5. **Search** — Full-text search with stemming. Multi-word queries use AND logic; quoted phrases for exact matches
6. **Learn** — AI tools save new discoveries via `mem_save` during sessions. Knowledge compounds automatically

## How it works with your AI tools

Once the MCP server is configured, your AI assistant automatically uses crossmem:

```
You: "How should I handle credentials in this new service?"

AI: Let me check crossmem for existing patterns...
    [calls mem_recall → finds credential masking in 3 of your projects]

    Based on your previous work across backend-api, mobile-app, and infra-tools,
    you consistently use a middleware layer for credential masking. Here's the
    pattern from your backend-api project:
    - Credentials stored in Secret Manager, never in env vars
    - API keys masked in logs via _mask_sensitive_headers()
    ...
```

No copy-pasting. No "I already solved this." Your AI assistant recalls patterns from every project you've worked on — automatically.

## MCP Server

crossmem runs as an MCP server so AI coding tools can search, recall, and save memories in real-time.

### Setup

Add to your tool's MCP config:

**Claude Code** (`~/.mcp.json` for global, or `.mcp.json` in project root):
```json
{
  "mcpServers": {
    "crossmem": {
      "command": "crossmem-server"
    }
  }
}
```

**Gemini CLI** (`~/.gemini/settings.json`):
```json
{
  "mcpServers": {
    "crossmem": {
      "command": "crossmem-server"
    }
  }
}
```

**VS Code / GitHub Copilot** (`.vscode/mcp.json` in project root, or user `settings.json`):
```json
{
  "servers": {
    "crossmem": {
      "command": "uvx",
      "args": ["--from", "crossmem", "crossmem-server"]
    }
  }
}
```

> **Note:** For Claude Code and Gemini CLI, if `crossmem-server` isn't on PATH, use the same `uvx` command shown in the Copilot config above.
>
> **Note:** `install-instructions` writes to `.github/copilot-instructions.md` in the **current project** (not global). Run `crossmem setup` from each project, or add it to your project template. Gemini instructions are global (`~/.gemini/GEMINI.md`).

### Tools

| Tool | Description |
|------|-------------|
| `mem_recall` | Load project context + cross-project patterns at session start (auto-detects project from cwd) |
| `mem_search` | Search across all memories (query, project filter, limit) |
| `mem_save` | Save a discovery during a session — immediately searchable |
| `mem_update` | Update a memory in place — preserves ID, optionally moves section/project |
| `mem_forget` | Delete a memory by ID (find IDs via `mem_search`) |
| `mem_get` | Get the full content of a memory by ID (search results are truncated) |
| `mem_init` | Index project documentation files (README, CLAUDE.md, etc.) for cross-tool recall |
| `mem_ingest` | Refresh the index when memory files change (auto-runs on server startup) |

### Start manually

```bash
crossmem serve    # starts MCP server on stdio (same as crossmem-server)
```

## Auto-recall (Claude Code hook)

The MCP server requires the LLM to decide when to call `mem_recall`. With auto-recall, memories are injected **deterministically** — no LLM decision needed.

```bash
crossmem setup           # installs hook + instructions + ingest (recommended)
crossmem install-hook    # or install just the hook
```

This adds a `SessionStart` hook to `~/.claude/settings.json` that runs `crossmem recall` on:
- **startup** — new session
- **resume** — returning to an existing session
- **compact** — context window compaction mid-session

Each recall auto-ingests the latest native memories from all tools, so long sessions stay fresh.

To remove: `crossmem install-hook --uninstall`

## Supported tools

| Tool | Ingestion |
|------|-----------|
| Claude Code | `~/.claude/projects/*/memory/*.md` |
| Gemini CLI | `~/.gemini/GEMINI.md` |
| GitHub Copilot | `~/Library/Application Support/Code/User/globalStorage/github.copilot-chat/memory-tool/memories/*.md` |

Ingestion is pluggable — PRs welcome for new tools.

## License

MIT
