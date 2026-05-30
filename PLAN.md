# Bugzilla RAG System — Redesign Plan

This document captures the full redesign plan for the Bugzilla RAG system, derived from an
interview-driven design session conducted on 2026-05-28. Every decision here was made deliberately.
This file is the single source of truth for the refactoring work.

---

## 1. Project Context & Goals

### What this system does
A local, offline Retrieval-Augmented Generation (RAG) system that ingests SUSE Bugzilla records,
embeds them into a vector database, and answers natural-language questions about historical bugs
using a locally-running LLM.

### Target audience
Public open-source tool. The default target is `bugzilla.suse.com`, but the design must be clean,
documented, and generalizable so others can adapt it for their own Bugzilla instances.

### Primary motivation for redesign
- The original full download takes **weeks** and indexing takes **days**. Development was blocked
  on production-scale data.
- The codebase had critical bugs (missing `query_bugzilla` function, CLI loop running at import
  time, hardcoded URLs, no secrets management). All fixed in the 2026-05-28 session.
- No automated tests existed. No software engineering best practices were followed.
- The redesign enables a fast, test-driven development workflow with small data sets, while
  preserving full production capability.

---

## 2. Architecture Overview

The system is a four-stage pipeline. Each stage is a standalone script with a `main()` function,
registered as a CLI command via `pyproject.toml` entry points.

```
[bugzilla.suse.com REST API]
         |
         | bugzilla-download [--limit N] [--since DATE]
         v
  data/bug_reports.jsonl     (append-only, one JSON object per line)
         |
         | bugzilla-index [--input FILE] [--db DIR] [--batch-size N]
         v
  chroma_db/                 (ChromaDB vector store, persisted to SQLite)
         |
         | bugzilla-query (CLI)
         | bugzilla-serve (Flask web UI)
         v
  [User: natural-language answers with source bug links]
```

### Stage responsibilities

| Stage | Script | CLI command | Input | Output |
|---|---|---|---|---|
| 1. Download | `download_bugzilla.py` | `bugzilla-download` | Bugzilla REST API | `data/bug_reports.jsonl` |
| 2. Index | `index_bugs_to_chroma.py` | `bugzilla-index` | `data/bug_reports.jsonl` | `chroma_db/` |
| 3. Query (CLI) | `query_cli.py` | `bugzilla-query` | `chroma_db/` + Ollama | stdout |
| 4. Serve (Web) | `app.py` | `bugzilla-serve` | `chroma_db/` + Ollama | HTTP :5000 |
| (shared) | `rag_engine.py` | — | `chroma_db/` + Ollama | Python API |

---

## 3. Development vs Production Workflow

### Core principle
The pipeline supports two modes using the **same code path**, controlled by flags:
- **Development mode**: small data set (hundreds of bugs), fast iteration, all tests pass.
- **Production mode**: full data set (550k+ bugs), resumable download, hours-long indexing.

### Dev workflow (fast iteration)
```bash
# 1. Download a small batch of real bugs for development/testing
bugzilla-download --limit 500 --output data/dev_bugs.jsonl

# 2. Index the small batch into a dev vector store
bugzilla-index --input data/dev_bugs.jsonl --db dev_chroma_db/

# 3. Run the test suite against the dev data
pytest

# 4. Query interactively
bugzilla-query --db dev_chroma_db/

# 5. Run the web interface
bugzilla-serve --db dev_chroma_db/
```

### Production workflow (full dataset)
```bash
# 1. Convert a legacy JSON array dump to JSONL (one-time, if migrating old data)
python /tmp/opencode/convert_json_to_jsonl.py   # uses ijson, streaming, no RAM spike

# 2. Fetch missing recent bugs (incremental, after initial data is in place)
bugzilla-download --since 2024-11-05 --output data/bug_reports.jsonl

# 3. Full index (hours; resumable; skips already-indexed bugs via ChromaDB ID lookup)
bugzilla-index --input data/bug_reports.jsonl --db chroma_db/ --batch-size 200

# 4. Serve
bugzilla-serve --db chroma_db/

# 5. Ongoing: daily/weekly incremental update
bugzilla-download --since LAST_RUN_DATE --output data/bug_reports.jsonl
bugzilla-index --input data/bug_reports.jsonl --db chroma_db/
```

### Running stages in parallel
The download and index stages can run simultaneously. The indexer processes whatever is in
the JSONL file at startup; run the indexer again after the download completes to pick up the
remainder. The delete-then-reindex logic handles any updated bugs cleanly.

```bash
screen -dmS bugzilla-download bash -c 'bugzilla-download --since DATE --output data/bug_reports.jsonl >> logs/download.log 2>&1'
screen -dmS bugzilla-index   bash -c 'bugzilla-index --input data/bug_reports.jsonl --db chroma_db/ >> logs/index.log 2>&1'
```

---

## 4. Stage 1: Download (`download_bugzilla.py`)

### Storage format: JSONL (Newline-Delimited JSON)
- One JSON object per line; append-only.
- Streamable: the indexer reads line-by-line without loading the full file into RAM.
- Inspectable with `wc -l`, `jq`, `tail`, standard Unix tools.
- No extra dependencies beyond stdlib.

### Resumption strategy
- Track the **highest bug ID seen** in the output file (scan the full file for max ID at startup).
- For a full (unfiltered) download: resume from `offset = existing_line_count`.
- For a `--since` incremental run: always start from `offset = 0` against the filtered result set
  (the `last_change_time` API filter returns a fresh result set independent of the full dataset).

### Incremental updates (`--since DATE`)
- Uses the Bugzilla REST API `last_change_time` parameter.
- Catches both new bugs and bugs updated (new comments, status changes) since the cutoff.
- Re-downloaded bugs are appended; the indexer handles deduplication via delete-then-reindex.

### API timeouts
- Bug list endpoint (`/rest/bug`): **60 second timeout**. `last_change_time` queries require
  full-table scans on the Bugzilla DB and take 7–8 seconds server-side; 10s was too tight.
- Comments endpoint (`/rest/bug/{id}/comment`): **30 second timeout**.

### CLI flags

| Flag | Description | Default |
|---|---|---|
| `--output FILE` | Path to output JSONL file | `data/bug_reports.jsonl` (from `.env`) |
| `--limit N` | Stop after N bugs (dev mode) | None (unlimited) |
| `--since DATE` | Fetch bugs changed on/after DATE (ISO 8601) | None |
| `--verbose` | Set log level to DEBUG | INFO |

### API key rotation
- 29 API keys stored in `.env` as `BUGZILLA_API_KEYS` (comma-separated).
- Round-robin rotation on HTTP 429 (rate limit) or timeout.
- Observed frequency: ~1 rotation event per 70 bugs at the early (low-traffic) bug IDs.

### Error handling

| Error | Action |
|---|---|
| HTTP 429 | Rotate API key, sleep 60 s |
| HTTP 503 | Rotate API key, sleep 30 s |
| Timeout (bug list) | Rotate key, sleep 30 s, exponential backoff |
| Timeout (comments) | Rotate key, sleep 5 s |
| `KeyboardInterrupt` | Progress already saved (JSONL append); exit cleanly |

### Bug record schema (JSONL)
```json
{
  "bug_number": 123456,
  "title": "Some bug summary",
  "Product": "SUSE Linux Enterprise",
  "version": "15 SP4",
  "Component": "kernel",
  "Reported": "2023-01-15T10:30:00Z",
  "Status": "RESOLVED",
  "Comments": [
    {"name": "user@example.com", "date": "2023-01-15T10:30:00Z", "text": "..."}
  ]
}
```

---

## 5. Stage 1b: Legacy Data Migration (one-time)

The previous version of the project produced `~/bugs.json` — a single flat JSON array (3.8 GB,
546,623 bugs, through 2024-11-04). This was converted to JSONL using a streaming `ijson` parser
to avoid loading 3.8 GB into RAM.

```bash
bugzilla-env/bin/pip install ijson
bugzilla-env/bin/python /tmp/opencode/convert_json_to_jsonl.py
# 546,623 bugs converted in 20 seconds at ~27,000 bugs/sec
```

This is a one-time operation. The script is not part of the production pipeline.

---

## 6. Stage 2: Index (`index_bugs_to_chroma.py`)

### Resumption: ChromaDB ID tracking
- No pickle checkpoint file. On startup, query ChromaDB for all existing document IDs
  (`collection.get(include=[])["ids"]`) and build an in-memory set.
- Any bug whose ID is already in the set is skipped (full run) or deleted-then-reindexed
  (incremental run after `--since` download).

### Batch size
- Default: 200 bugs per ChromaDB write.
- Configurable via `--batch-size N` or `.env` `INDEX_BATCH_SIZE`.
- ChromaDB flushes to SQLite periodically, causing brief rate dips. This is normal.

### Observed performance

**v1 (flat, 2026-05-28)** — single document per bug, no chunking:
- Rate: ~51 bugs/sec; 571,325 bugs in ~3 hours; 20 GB ChromaDB

**v2 (chunked, 2026-05-29)** — overlapping chunks, noise-filtered:
- Rate: 5–26 bugs/sec (varies by comment density of current segment)
- 578,982 bugs → ~3.5M chunks; projected ~29 GB ChromaDB
- Early bugs (2004 era, few comments): ~26 bugs/sec
- Dense SUSE engineering bugs (kernel CVEs, L3 threads): ~5 bugs/sec
- Total time: ~15 hours

### Disk requirements (v2 chunked scheme)
| Item | Size |
|---|---|
| `data/bug_reports.jsonl` | 4.0 GB (579k bugs) |
| `chroma_db/` (v2, projected) | ~29 GB |
| `test_chroma_db/` | 36 MB (300 bugs) |
| `dev_chroma_db_v2/` | 9 MB (200 bugs) |
| **Total** | **~34 GB** |

Minimum recommended free disk: **40 GB** with margin.

### Embedding model
- `sentence-transformers/all-MiniLM-L6-v2` (384-dim, 22M params, CPU-optimized).
- Configurable via `.env` `EMBEDDING_MODEL`.
- Must match between index time and query time.

### Metadata stored per document
- `bug_id`, `title`, `product`, `component`, `status`, `reported`

### CLI flags

| Flag | Description | Default |
|---|---|---|
| `--input FILE` | Path to JSONL file | `data/bug_reports.jsonl` (from `.env`) |
| `--db DIR` | ChromaDB directory | `chroma_db/` (from `.env`) |
| `--batch-size N` | Bugs per write batch | 200 |
| `--embed-model MODEL` | HuggingFace model name | from `.env` |
| `--verbose` | Debug logging | INFO |

---

## 7. Shared RAG Engine (`rag_engine.py`)

New file extracted from the original conflated `query_interface.py`. Clean importable module —
no side effects on import.

### Public API
```python
from rag_engine import init, query_bugzilla, OllamaNotAvailableError, ChromaNotReadyError

init(chroma_dir="chroma_db/")   # optional explicit init; fails fast if services unavailable

result = query_bugzilla("What kernel CVE bugs were fixed in 2024?")
# Returns:
# {
#     "answer": "...",
#     "source_documents": [...],   # LangChain Document objects with metadata
#     "elapsed_time": 33.2         # seconds
# }
```

### Ollama availability check
Checks `OLLAMA_BASE_URL` with a 3-second timeout on init. Raises `OllamaNotAvailableError`
with a human-readable message including the systemctl command to start Ollama.

### Configuration (`.env`)
| Variable | Description | Default |
|---|---|---|
| `EMBEDDING_MODEL` | HuggingFace embedding model | `sentence-transformers/all-MiniLM-L6-v2` |
| `OLLAMA_MODEL` | Ollama model name | `qwen3-coder:30b` |
| `OLLAMA_BASE_URL` | Ollama API endpoint | `http://localhost:11434` |
| `OLLAMA_TEMPERATURE` | LLM temperature | `0.1` |
| `CHROMA_DIR` | ChromaDB directory | `chroma_db/` |
| `RAG_TOP_K` | Documents retrieved per query | `3` |

---

## 8. LLM Model Selection

A benchmark was run on 2026-05-28 against the three text-generation models available on this
machine, using two RAG queries against `dev_chroma_db/` (200 bugs).

| Model | Params | Quant | Avg time/query | Answer quality |
|---|---|---|---|---|
| `llama3:latest` | 8B | Q4_0 | 38.8s | Correct but terse; misses detail |
| `qwen2.5:7b` | 7.6B | Q4_K_M | 57.9s | Well-structured; slowest |
| **`qwen3-coder:30b`** | **30.5B** | **Q4_K_M** | **40.2s** | **Most thorough; best reasoning** |

**Selected: `qwen3-coder:30b`**

Rationale: despite 30B parameters, Q4_K_M quantization on this CPU is efficient enough to
match the 8B llama3 in wall-clock time, while producing significantly more thorough answers.
It correctly says "I don't know" when the retrieved context is insufficient (no hallucination).
The other models available (`qwen3-vl`, `qwen2.5vl:7b`) are vision models — not relevant here.
Embedding-only models (`mxbai-embed-large`, `nomic-embed-text`, `qwen3-embedding`) are not
suitable for generation.

---

## 9. Stage 3: CLI Query (`query_cli.py`)

- Imports `query_bugzilla` from `rag_engine`.
- Multi-line input terminated by `###` on its own line.
- Returns `None` on EOF — loop exits cleanly (fixes original infinite loop bug).
- Prints answer + elapsed time + top-K source snippets with bug ID and title.

### CLI flags

| Flag | Description |
|---|---|
| `--db DIR` | Override ChromaDB path |
| `--top-k N` | Override retrieved document count |
| `--verbose` | Debug logging |

---

## 10. Stage 4: Web Interface (`app.py`)

### Framework
Flask (retained). Dev server only — no Gunicorn needed for personal use.

### Template separation
HTML extracted to `templates/index.html` (Jinja2). No inline HTML in Python.

### Routes

| Route | Method | Description |
|---|---|---|
| `/` | GET | Render query form |
| `/` | POST | Run RAG, render results |
| `/status` | GET | JSON: active request count |
| `/eta` | GET | JSON: estimated wait time in seconds |

### Source links
`https://bugzilla.suse.com/show_bug.cgi?id={bug_id}` — configurable via `.env`
`BUGZILLA_BASE_URL`.

### CLI flags

| Flag | Description | Default |
|---|---|---|
| `--db DIR` | Override ChromaDB path | from `.env` |
| `--port N` | HTTP port | `5000` |
| `--host ADDR` | Bind address | `0.0.0.0` |

---

## 11. Configuration (`.env`)

```bash
# Bugzilla connection
BUGZILLA_BASE_URL=https://bugzilla.suse.com/rest/bug
BUGZILLA_API_KEYS=key1,key2,...,key29   # comma-separated, no spaces

# Data paths
BUGZILLA_JSONL=data/bug_reports.jsonl
CHROMA_DIR=chroma_db/

# Embedding
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2

# LLM (Ollama)
OLLAMA_MODEL=qwen3-coder:30b
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_TEMPERATURE=0.1

# RAG parameters
RAG_TOP_K=3
INDEX_BATCH_SIZE=200
```

The actual `.env` is gitignored. `.env.example` (without real keys) is committed.

---

## 12. Testing Strategy

### Framework
`pytest` with mocks for all external services (Bugzilla API, ChromaDB, Ollama).

### Fixture data
- Real Bugzilla records downloaded with `--limit 200` on 2026-05-28.
- File: `data/dev_bugs.jsonl` (200 bugs, bugs #1–#200).
- A curated subset of ~100 will be committed to `tests/fixtures/sample_bugs.jsonl`.

### Test suite structure
```
tests/
  fixtures/
    sample_bugs.jsonl         # ~100 real Bugzilla records (committed to repo)
  conftest.py                 # Shared fixtures: sample bugs, temp dirs, mock clients
  test_download.py            # Unit tests: pagination, 429 rotation, --limit, --since, resumption
  test_index.py               # Unit tests: skip indexed, delete-reindex, batch size, empty docs
  test_rag_engine.py          # Unit tests: OllamaNotAvailableError, dict shape, TOP_K
  test_app.py                 # Integration: Flask test client, /status, /eta routes
```

### Key test scenarios
- Downloader: `--since` resets offset to 0 (not existing_count); `--limit` stops exactly at N;
  key rotation on 429; resumption from last bug ID.
- Indexer: ChromaDB ID set correctly populated at startup; delete-then-reindex for updated bugs;
  empty documents are skipped without crashing.
- RAG engine: `OllamaNotAvailableError` when Ollama is unreachable; result dict always has
  `answer`, `source_documents`, `elapsed_time` keys.
- Web: POST `/` returns 200 and contains answer text; `/status` and `/eta` return valid JSON.

---

## 13. Project Structure

```
download-bugzilla/
  download_bugzilla.py        # Stage 1: Download (refactored; captures 8 new API fields)
  index_bugs_to_chroma.py     # Stage 2: Index — v2: noise filter, chunking, richer metadata
  rag_engine.py               # Shared: RAG engine module (no side effects on import)
  query_cli.py                # Stage 3: Interactive CLI query interface
  query_interface.py          # DEPRECATED — superseded by query_cli.py + rag_engine.py
  app.py                      # Stage 4: Flask web interface (to be refactored)
  test_env.py                 # Environment validation script
  setup_env.sh                # openSUSE/SUSE system setup script
  requirements.txt            # Python dependencies
  .env                        # Secrets and config (gitignored)
  .env.example                # Configuration template (copy to .env)
  .gitignore
  PLAN.md                     # This file
  README.md                   # Updated project documentation
  templates/
    index.html                # Flask Jinja2 template (web UI — to be created)
  tests/                      # To be implemented
    fixtures/
      sample_bugs.jsonl
    conftest.py
    test_download.py
    test_index.py
    test_rag_engine.py
    test_app.py
  data/                       # Runtime data (gitignored)
    bug_reports.jsonl         # Full production JSONL (578,982 bugs, 4.0 GB)
    dev_bugs.jsonl            # Dev subset (200 bugs, 2004-era)
    test_bugs.jsonl           # Test subset (300 recent bugs, with new fields)
  chroma_db/                  # Production ChromaDB v2 — chunked (gitignored, ~29 GB)
  test_chroma_db/             # Test ChromaDB (300 bugs, 1,859 chunks, 36 MB)
  dev_chroma_db_v2/           # Dev ChromaDB v2 (200 bugs, 855 chunks, 9 MB)
  logs/                       # Runtime logs (gitignored)
    download_incremental.log  # Incremental download log (complete)
    download_test.log         # Test batch download log
    index_v2.log              # Chunked indexer log (in progress)
  bugzilla-env/               # Python virtual environment (gitignored)
  LICENSE                     # GNU GPL v2
```

---

## 14. Python Environment

### Runtime
- Python 3.13.13 (system, openSUSE Tumbleweed)
- Virtual environment: `bugzilla-env/` (created with `python3 -m venv bugzilla-env`)
- System package manager: `zypper` (SUSE)

### Key installed packages (actual versions as of 2026-05-28)
| Package | Version | Role |
|---|---|---|
| `python-dotenv` | 1.2.2 | `.env` loading |
| `requests` | 2.34.2 | HTTP client |
| `tqdm` | 4.67.3 | Progress bars |
| `langchain` | 1.3.2 | RAG orchestration |
| `langchain-classic` | 1.0.7 | `RetrievalQA` chain (moved here from `langchain`) |
| `langchain-chroma` | 1.1.0 | ChromaDB adapter |
| `langchain-huggingface` | 1.2.2 | Embedding adapter |
| `langchain-ollama` | 1.1.0 | Ollama LLM adapter |
| `sentence-transformers` | 5.5.1 | MiniLM embedding runtime |
| `chromadb` | 1.5.9 | Vector store |
| `ijson` | (latest) | Streaming JSON parser (for legacy migration) |
| `Flask` | (latest) | Web interface |
| `markdown2` | (latest) | Markdown rendering in web UI |

### Import path changes vs original code
- `langchain.docstore.document.Document` → `langchain_core.documents.Document`
- `langchain.chains.RetrievalQA` → `langchain_classic.chains.RetrievalQA`

---

## 15. Packaging (`pyproject.toml`) — to be created

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "bugzilla-rag"
version = "2.0.0"
description = "Local RAG system for querying Bugzilla bug reports"
license = {text = "GPL-2.0-only"}
requires-python = ">=3.11"
dependencies = [
    "Flask",
    "markdown2",
    "tqdm",
    "python-dotenv",
    "requests",
    "ijson",
    "langchain",
    "langchain-classic",
    "langchain-chroma",
    "langchain-huggingface",
    "langchain-community",
    "langchain-ollama",
    "sentence-transformers",
    "chromadb",
]

[project.optional-dependencies]
dev = [
    "pytest",
    "pytest-mock",
]

[project.scripts]
bugzilla-download = "download_bugzilla:main"
bugzilla-index    = "index_bugs_to_chroma:main"
bugzilla-query    = "query_cli:main"
bugzilla-serve    = "app:main"
```

Note: version pins will be added once the full stack is validated against the production index.

---

## 16. Logging

- Library: Python stdlib `logging`.
- Default level: `INFO`. `--verbose` sets `DEBUG`.
- Format: `%(asctime)s [%(levelname)s] %(name)s: %(message)s`
- `tqdm` progress bars for long-running stages.
- Log files written to `logs/` (gitignored).

---

## 17. Setup & Installation

### On openSUSE / SUSE
```bash
# System deps (Ollama, Python)
sudo zypper install python313 ollama
sudo systemctl enable --now ollama
ollama pull qwen3-coder:30b

# Project
python3 -m venv bugzilla-env
bugzilla-env/bin/pip install -e .
cp .env.example .env
# Edit .env — add BUGZILLA_API_KEYS
```

### On any platform
```bash
# Prerequisites: Python 3.11+, Ollama with qwen3-coder:30b pulled
pip install -e .
cp .env.example .env
```

### Monitor long-running jobs
```bash
# Reattach to screen sessions
screen -r bugzilla-download
screen -r bugzilla-index

# Live log tailing
tail -f logs/download_incremental.log
tail -f logs/index.log

# Progress snapshot
wc -l data/bug_reports.jsonl
ls -lh chroma_db/chroma.sqlite3
```

---

## 18. Bugs Fixed from Original Codebase

| # | Original bug | Fix |
|---|---|---|
| 1 | `query_bugzilla` not defined in `query_interface.py` | Extracted to `rag_engine.py` |
| 2 | CLI loop runs at import time (breaks `app.py`) | `if __name__ == "__main__": main()` guard |
| 3 | `app.py` accesses `result["elapsed_time"]` (KeyError) | `rag_engine` returns dict with `elapsed_time` |
| 4 | `bugzilla.suse.com` hardcoded in `fetch_comments()` | Read from `BUGZILLA_BASE_URL` in `.env` |
| 5 | API keys hardcoded in Python source | Moved to `.env` / `BUGZILLA_API_KEYS` |
| 6 | No version pins in `requirements.txt` | Tracked in `pyproject.toml` |
| 7 | Pickle checkpoint can desync from ChromaDB | Replaced with ChromaDB ID set query |
| 8 | Single flat JSON file loaded entirely into RAM | JSONL with streaming line-by-line reads |
| 9 | `--since` offset wrongly set to `existing_count` | Reset to 0 for filtered queries |
| 10 | Bug list timeout 10s too short for `last_change_time` queries | Raised to 60s (server takes 7–8s) |
| 11 | EOF in CLI query loop caused infinite spin | `get_multiline_input()` returns `None` on EOF |
| 12 | `langchain.docstore.document` import broken (API changed) | Updated to `langchain_core.documents` |
| 13 | `langchain.chains.RetrievalQA` import broken (moved) | Updated to `langchain_classic.chains` |
| 14 | 43.8% of bugs silently truncated by MiniLM 512-token limit | Overlapping chunk strategy — every comment is retrievable |
| 15 | 19.5% of comment tokens are noise (bots, attachments, quotes) | `_is_noise()` filter strips before embedding |
| 16 | Resolution/fix details in late comments unreachable | `_cR` resolution chunk always created as a separate document |
| 17 | CVE IDs, resolution status, priority not queryable | Captured in downloader; stored as ChromaDB metadata |
| 18 | Chunk deduplication broken for multi-chunk bugs | `get_indexed_bug_ids()` queries by `bug_id` metadata, not doc ID |

---

## 19. Open Questions (Deferred)

1. **Web UI authentication**: Flask binds to `0.0.0.0`. For any shared use, add HTTP basic
   auth or restrict to localhost.

2. **Ollama model pinning**: `ollama pull qwen3-coder:30b` pulls the latest tag. Pin to a
   specific digest for reproducibility.

3. **`app.py` refactor**: The web interface still has inline HTML and other original-code issues.
   Extract to `templates/index.html` and align with the new `rag_engine` API.

4. **`pyproject.toml`**: Not yet created. `requirements.txt` is still the active dependency file.
   Create `pyproject.toml` and entry points once the full pipeline is validated.

5. **Test suite**: Not yet implemented. Implement after `app.py` refactor and `pyproject.toml`
   creation.

6. **`query_interface.py`**: Old file still present. Remove once `app.py` is updated to import
   from `rag_engine.py`.

7. **RAG_TOP_K tuning**: With chunked retrieval, `k=5` retrieves chunks that may belong to
   only 2–3 unique bugs. Evaluate whether increasing to `k=8–10` and deduplicating by `bug_id`
   gives better answer coverage.

8. **Chunk size tuning**: `CHUNK_WINDOW=5, STRIDE=3` was chosen heuristically. Evaluate
   against the production index — larger window may help for bugs with very long individual
   comments that still exceed 512 tokens per chunk.

9. **Re-download with new fields**: The production JSONL was mostly converted from the old
   `bugs.json` which lacks `resolution`, `priority`, `severity`, `aliases`, `keywords`,
   `whiteboard`, `url`. Only bugs fetched after 2026-05-28 have these fields. A full
   re-download is needed to populate these fields for all 578k bugs.

---

## 20. Embedding Quality Improvements (implemented 2026-05-28)

### Problem identified
Analysis of 5,000 randomly sampled bugs revealed:
- **43.8%** of bugs exceed MiniLM's 512-token limit — their content was silently truncated
- **19.5%** of all comments are noise (bot messages, attachment notices, heavily-quoted replies)
- Noise filtering alone rescues only 8% of truncated bugs — chunking is the critical fix
- Key API fields (`resolution`, `priority`, `aliases`/CVE IDs, `whiteboard`) were not captured
  by the downloader or stored in ChromaDB metadata

### Solutions implemented

#### 1. Noise filtering (`_is_noise()` in `index_bugs_to_chroma.py`)
Strips before embedding:
- Bot/autogenerated IBS/OBS build system messages (9.4% of all comments)
- `Created attachment NNNNN` notices (7.2%)
- Comments where >60% of lines are `>`-quoted reply text (2.9%)

#### 2. Overlapping chunk strategy (`bug_to_chunks()`)
Each bug is split into multiple ChromaDB documents:
- **`_c0` (description)**: header + first substantive comment — always present
- **`_c1.._cN` (comments)**: sliding window of 5 comments, stride 3 (2-comment overlap)
- **`_cR` (resolution)**: header + last substantive comment — always present
- Doc IDs: `{bug_id}_c0`, `{bug_id}_c1`, …, `{bug_id}_cR`
- Deduplication: `get_indexed_bug_ids()` queries by metadata `bug_id`, not doc ID

Result: 200 dev bugs → 855 chunks (4.3×); 300 test bugs → 1,859 chunks (6.2×)

#### 3. Richer metadata
New fields stored per chunk in ChromaDB:
`resolution`, `priority`, `severity`, `aliases` (CVE IDs), `keywords`, `whiteboard`,
`url`, `last_changed`

#### 4. Downloader extended
`download_bugzilla.py` `bug_record` now captures 8 additional API fields:
`last_change_time`, `resolution`, `priority`, `severity`, `alias`, `keywords`,
`whiteboard`, `url`

### Validation (2026-05-28, `test_chroma_db`, 300 recent bugs)

| Query | Key finding |
|---|---|
| Docker CVEs | CVE alias metadata surfaced directly; synthesised CVE list 2014–2025 |
| btrfs hangs | `[NORESPONSE]` resolution chunk retrieved — unresolved bug correctly identified |
| CVE-2014-6407 trace | "Yes, fixed in Docker 1.8.3" — resolution from mid-thread, previously invisible |
| Kernel privilege escalation | Sourced from `[comments]` chunks, mid-thread content confirmed reachable |
| NetworkManager | `[resolution]` chunks for WONTFIX/INVALID bugs surfaced — previously impossible |

---

## 21. Session Log — 2026-05-28

Chronological record of what was done across the full session.

| Time | Action | Result |
|---|---|---|
| Morning | Design interview (16 questions across 12 topics) | All decisions captured in this PLAN |
| 12:22 | `.env` created and formatted; `.gitignore` created | Secrets secured |
| 12:24 | `download_bugzilla.py` refactored | `.env` config, JSONL output, `--limit`, `--since`, `main()` |
| 12:25 | Dev download: `--limit 200` | 200 bugs in 237s; key rotation worked; JSONL verified |
| 12:33 | `index_bugs_to_chroma.py` refactored (v1) | ChromaDB ID tracking, richer metadata, streaming JSONL |
| 12:33 | Dev index: 200 bugs → `dev_chroma_db/` | 4s, 3.13 MB; fixed `langchain_core` import |
| 12:37 | `rag_engine.py` created; `query_cli.py` created | Fixed `langchain_classic` import; EOF loop fixed |
| 12:37 | End-to-end query test (dev data, llama3) | Answer in 13.8s; pipeline confirmed working |
| ~13:00 | LLM benchmark: llama3 vs qwen2.5:7b vs qwen3-coder:30b | `qwen3-coder:30b` selected |
| 13:01 | Discovered `~/bugs.json`: 546,623 bugs, 3.8 GB, through 2024-11-04 | No re-download needed |
| 13:01 | Converted `bugs.json` → `data/bug_reports.jsonl` using `ijson` | 20s, streaming, no RAM spike |
| 13:43 | Incremental download (`--since 2024-11-05`) first attempt | Failed: offset bug |
| 13:44 | Fixed offset bug; second attempt | Failed: 10s timeout too short for server-side scan |
| 13:44 | Raised bug list timeout 10s→60s, comments 10s→30s | Stable; server takes 7–8s per page |
| 15:52 | Incremental download running in screen | Fetching bugs changed since 2024-11-04 |
| 15:54 | Old-scheme full index started in screen | 552,577 bugs at ~51 bugs/sec (flat, 1 doc/bug) |
| 16:03 | Status check | 20,400 indexed; 251 MB; download at 552,630 |
| 22:24 | Old-scheme index **finished** | 571,325 bugs, 20 GB ChromaDB (flat scheme) |
| ~23:00 | Embedding quality brainstorm | Analysed 5,000 bugs: 43.8% truncated, 19.5% noise |
| 23:00 | `index_bugs_to_chroma.py` rewritten (v2) | Noise filter, chunking, richer metadata, semantic anchors |
| 23:00 | `download_bugzilla.py` updated | Captures 8 new API fields per bug |
| 23:01 | Dev validation: `dev_chroma_db_v2` (200 bugs, 855 chunks) | Chunking confirmed working |
| 23:30 | Test download: 300 recent bugs → `data/test_bugs.jsonl` | 50 min, real SUSE engineering bugs |
| 23:30 | Test index: `test_chroma_db` (300 bugs, 1,859 chunks, 33.6 MB) | All 5 test queries passed |
| 23:52 | Old flat `chroma_db/` deleted (21 GB reclaimed) | 76 GB free |
| 23:52 | **New chunked index started** (`screen bugzilla-index-v2`) | 576,433 bugs → `chroma_db/` |

## 22. Session Log — 2026-05-29

| Time | Action | Result |
|---|---|---|
| 00:36 | Incremental download **finished** | 578,982 bugs total in `data/bug_reports.jsonl` |
| 09:00 | Indexer at 64.7% | 373,200 bugs, 1,766,313 chunks, 19 GB DB |
| 12:45 | Indexer at 70% | 401,200 bugs, 1,905,437 chunks, 21 GB DB |
| 12:53 | Indexer briefly paused (SIGSTOP) | Confirmed truly paused: state T, DB not growing |
| 12:55 | Indexer resumed (SIGCONT) | Continuing at ~5 bugs/sec (dense SUSE engineering bugs) |

### Current state (as of 12:55, 2026-05-29)
- **Download**: complete — 578,982 bugs in `data/bug_reports.jsonl`
- **Indexer v2**: running in `screen bugzilla-index-v2`, ~70% done, ETA ~13:47
- **Schema**: chunked (noise-filtered, overlapping windows, semantic anchors, richer metadata)
- **Dev pipeline**: fully validated (`test_chroma_db`, 5 query types confirmed)
- **Production pipeline**: `chroma_db/` being built — query available after ~14:00

---

## 23. Session Log — 2026-05-29 (crash recovery) and 2026-05-30

### What happened
The laptop crashed at ~15:07 on 2026-05-29, killing the `screen bugzilla-index-v2` session.
The indexer had reached bug ~421,600 of 578,982 (73%), 22 GB DB, ~2M chunks written.

### Recovery — 2026-05-29 ~15:15

| Time | Action | Result |
|---|---|---|
| ~15:15 | Session resurrected; state assessed | Download complete; indexer dead at 73%; screen gone |
| ~15:17 | Restart attempt 1 | **Failed**: `ChromaDB InternalError: too many SQL variables` |
| ~15:17 | Root cause: `get_indexed_bug_ids()` called `vectorstore.get(include=["metadatas"])` | Fetched all 2M+ chunk rows in one SQLite query, hitting variable limit |
| ~15:20 | Fix: replaced with `SELECT DISTINCT string_value FROM embedding_metadata WHERE key='bug_id'` | Direct SQLite read; returns in milliseconds regardless of DB size |
| ~15:22 | Restart attempt 2 | **Failed**: `AttributeError: 'Chroma' object has no attribute '_persist_directory'` |
| ~15:22 | Fix: pass `chroma_dir` explicitly to `get_indexed_bug_ids()` instead of reading from object | Function signature updated: `get_indexed_bug_ids(vectorstore, chroma_dir)` |
| ~15:25 | Restart attempt 3 | Running — confirmed 421,800 unique bugs already indexed, skipping at ~7 bug/s |
| ~15:28 | Discovered second bug: `updated=29,800, skipped=0` | Already-indexed bugs being *deleted and re-indexed* instead of skipped |
| ~15:30 | Root cause: no `continue` in the `if bug_id in indexed_bugs` branch | Logic was correct for `--since` incremental mode but wrong for plain resumption |
| ~15:32 | Fix: added `--reindex` flag; default behavior skips already-indexed bugs | `reindex=False` adds `continue`; `--reindex` restores delete-then-reindex |
| ~15:35 | Restart attempt 4 — **successful** | Skipping at 31,000 bug/s; 421,736 skipped in seconds; real indexing started |
| ~17:00 | Status check | Indexing new bugs at 3–5 bug/s; ~157k remaining; ETA ~04:00 next morning |

### Completion — 2026-05-30

| Time | Action | Result |
|---|---|---|
| 12:39:16 | **Indexer finished** | 157,246 new bugs indexed, 837,617 chunks written, 421,736 skipped |
| 12:39:16 | Final DB state | 578,982 bugs total, **42 GB** `chroma_db/chroma.sqlite3` |
| 12:39:16 | Total runtime (resumed run) | 4 hours 25 minutes |

### Bugs fixed in `index_bugs_to_chroma.py` during recovery

| # | Bug | Fix |
|---|---|---|
| 19 | `vectorstore.get(include=["metadatas"])` hits SQLite variable limit at scale | Replaced with direct `SELECT DISTINCT` on `embedding_metadata` table |
| 20 | `'Chroma' object has no attribute '_persist_directory'` | Pass `chroma_dir` as explicit parameter to `get_indexed_bug_ids()` |
| 21 | Resumption re-indexes all already-indexed bugs instead of skipping them | Added `--reindex` flag; default is skip (plain `continue`) |

### Final production state (as of 2026-05-30 12:39)
- **Download**: complete — 578,982 bugs in `data/bug_reports.jsonl` (4.0 GB)
- **Index**: complete — `chroma_db/` (42 GB, chunked v2 scheme, 578,982 bugs, ~3.5M chunks)
- **Pipeline**: fully operational; query with `query_cli.py --db chroma_db` or `app.py --db chroma_db`

### Disk usage (final)
| Item | Size |
|---|---|
| `data/bug_reports.jsonl` | 4.0 GB (578,982 bugs) |
| `chroma_db/` (v2 chunked) | 42 GB |
| `test_chroma_db/` | 36 MB (300 bugs) |
| `dev_chroma_db_v2/` | 9 MB (200 bugs) |

### Open items remaining (before 2026-05-30 afternoon)
- See section 19 (Open Questions) — unchanged
- Test suite (section 12) still not implemented
- `app.py` refactor (section 19.3) still pending
- `pyproject.toml` (section 15) still to be created

---

## 24. Session Log — 2026-05-30 (demo creation)

| Time | Action | Result |
|---|---|---|
| ~14:00 | `app.py` refactored | Fixed `query_interface` → `rag_engine` import; extracted inline HTML to `templates/index.html`; added `argparse` CLI; clean error handling for `OllamaNotAvailableError` / `ChromaNotReadyError`; `BUGZILLA_BUG_URL` derived from `.env`; removed `debug=True` |
| ~14:00 | `templates/index.html` created | Clean Jinja2 template; accurate copy (578,982 bugs, 42 GB, `qwen3-coder:30b`, `all-MiniLM-L6-v2`); status bar; source cards showing product/status/resolution metadata and clean body snippet |
| ~14:00 | `demo_queries.py` created | Non-interactive demo runner; 5 curated showcase queries (Docker CVEs, btrfs hangs, kernel privilege escalation, NetworkManager regressions, SLE 15 SP4 kernel CVEs); formatted output with source links; summary table; `--queries N` flag to run a subset |
| ~14:00 | `markdown2` and `Flask` installed into venv | Were missing from venv despite being in `requirements.txt` |

### Files created/modified
| File | Change |
|---|---|
| `app.py` | Full rewrite — imports `rag_engine`, uses `render_template`, proper `main()` with argparse |
| `templates/index.html` | New — clean Jinja2 template, extracted from old inline `TEMPLATE` string |
| `demo_queries.py` | New — 5-query non-interactive demo runner |

### Open items (updated)
- `app.py` refactor: **done**
- Test suite (section 12): not yet implemented
- `pyproject.toml` (section 15): not yet created
- `query_interface.py`: old file still present; safe to delete once nothing else imports it

---

## 25. Packaging & Deployment (2026-05-30)

### Design decision: why not a self-contained Docker image with the DB?

The ChromaDB is 46 GB. Docker images are not designed for multi-gigabyte binary
blobs — registry push/pull is impractical and image layers are not suited for
random-access binary databases. The correct split is:

- **Image**: application code + JSONL (4 GB) + embedding model weights
- **Volume**: `chroma_db/` (46 GB) — provided at runtime via bind mount or rsync

### Two deployment modes

| Mode | When to use | Index time |
|---|---|---|
| **rsync pre-built DB** | Teammates or servers where you control setup | ~0 (minutes for rsync) |
| **Auto-index on first boot** | Fresh server with no DB | ~15 hours |

The entrypoint detects which mode applies automatically.

### Files created

| File | Purpose |
|---|---|
| `Dockerfile` | Bakes in Python deps + JSONL + MiniLM model; mounts `chroma_db/` at `/data/chroma_db` |
| `entrypoint.sh` | Checks for existing DB; indexes if absent; starts Flask |
| `docker-compose.yml` | Orchestrates `app` + `ollama` services; `chroma_db/` as bind mount |
| `.dockerignore` | Excludes venv, DB dirs, logs, `.env`, markdown from build context |
| `requirements.txt` | Pinned versions (updated from unpinned) |

### Disk budget for the image

| Layer | Approximate size |
|---|---|
| python:3.13-slim base | ~120 MB |
| System packages (git, curl, build-essential) | ~80 MB |
| Python dependencies (PyTorch + all-MiniLM) | ~3.5 GB |
| Application source | ~1 MB |
| `data/bug_reports.jsonl` | ~4 GB |
| MiniLM model cache | ~90 MB |
| **Total compressed image** | **~5–6 GB** |

### Deployment workflow (rsync path — recommended)

```bash
# On the source machine (this one):
rsync -avz --progress --checksum \
  /home/balogh/download-bugzilla/chroma_db/ \
  user@target-host:/opt/bugzilla-rag/chroma_db/

# On the target host:
git clone <repo>     # or scp the project directory
cp .env.example .env
# Edit .env — set BUGZILLA_API_KEYS if needed; adjust OLLAMA_MODEL

# Pull the LLM model once:
docker compose run --rm ollama ollama pull qwen3-coder:30b

# Start everything:
docker compose up -d
docker compose logs -f
# App available at http://target-host:5000 once healthy
```

### Deployment workflow (auto-index path — fresh server)

```bash
docker compose up -d
docker compose logs -f app   # watch ~15h indexing progress
# App starts serving automatically when indexing finishes
```

### What is NOT in the image

No data is baked into the image. The image is code-only (~3.5 GB with PyTorch
and the MiniLM model weights). All data is provided at runtime via bind mounts:

| Path in container | What | How |
|---|---|---|
| `/data/chroma_db` | 46 GB vector index | rsync from data owner |
| `/data/bug_reports.jsonl` | 4 GB raw JSONL | optional; only for re-indexing |

### OLLAMA_BASE_URL in container context

Inside the Compose network, `app` reaches Ollama at `http://ollama:11434`
(the service name, not `localhost`). This is set as the default in both
`docker-compose.yml` and the `Dockerfile ENV` block. The `.env` on the host
must **not** override this to `localhost` or queries will fail.

### Exact handover procedure for a teammate

**You give them:** a git clone URL (or zip of the repo — no data included).

**They do:**
```bash
# 1. Clone
git clone <repo-url>
cd download-bugzilla

# 2. Config
cp .env.example .env
# No edits needed for basic use

# 3. Build the image (~20 min first time)
docker compose build

# 4. Pull the LLM model into Ollama (~18 GB, one-time)
docker compose up -d ollama
docker compose exec ollama ollama pull qwen3-coder:30b

# 5. Tell you their server IP — you rsync the DB to them
#    (you run this on YOUR machine):
rsync -avz --progress \
  /home/balogh/download-bugzilla/chroma_db/ \
  teammate@their-host:/path/to/download-bugzilla/chroma_db/

# 6. Start the app
docker compose up -d app
docker compose logs -f app

# 7. Open http://localhost:5000 (or http://their-host:5000)
```

**Total disk required on teammate's machine:** ~72 GB
(46 GB chroma_db + 18 GB Ollama model + 6 GB Docker image + overhead)

### Open items (updated)
- Test suite (section 12): not yet implemented
- `pyproject.toml` (section 15): not yet created
- `query_interface.py`: old file still present; safe to delete

---

*This plan was produced through a structured design interview on 2026-05-28 and updated
throughout the implementation sessions of 2026-05-28, 2026-05-29 (crash recovery),
2026-05-30 (demo), and 2026-05-30 (Docker packaging).*
*It supersedes the original README.md as the canonical design document.*
