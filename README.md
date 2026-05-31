# Bugzilla RAG System

A local, offline Retrieval-Augmented Generation (RAG) system that downloads SUSE Bugzilla
records, embeds them into a vector database, and answers natural-language questions about
historical bugs using a locally-running LLM. No cloud services required.

Originally described in the blog post:
[Building a Local Bugzilla RAG System](https://bzoltan1.github.io/building-a-local-bugzilla-rag-system/)

---

## Overview

The system is a four-stage pipeline:

```
[bugzilla.suse.com REST API]
         |
         | bugzilla-download [--limit N] [--since DATE]
         v
  data/bug_reports.jsonl       (append-only, streaming, resumable)
         |
         | bugzilla-index [--input FILE] [--db DIR]
         v
  chroma_db/                   (ChromaDB vector store, ~46 GB for 579k bugs)
         |
         | bugzilla-query      (interactive CLI)
         | bugzilla-serve      (Flask web UI on :5000)
         v
  [Natural-language answers with links to source bug reports]
```

| Stage | Script | Purpose |
|---|---|---|
| 1 | `download_bugzilla.py` | Fetch bugs + comments from Bugzilla REST API |
| 2 | `index_bugs_to_chroma.py` | Embed bugs into overlapping chunks, store in ChromaDB |
| 3 | `query_cli.py` | Interactive CLI query interface |
| 4 | `app.py` | Flask web interface |
| — | `rag_engine.py` | Shared RAG engine (imported by stages 3 and 4) |

---

## Deploying with Docker (recommended)

This is the fastest way to get the system running. The Docker image contains only the
application code and model weights — **no bug data**. You receive the ChromaDB index
separately from the data owner via rsync.

### Prerequisites

| Requirement | Notes |
|---|---|
| Docker Engine + Docker Compose | See installation instructions below |
| ~72 GB free disk | 46 GB index + 18 GB LLM model + 6 GB image + overhead |
| Network access to the data owner | For the rsync step |

#### Installing Docker on openSUSE / SUSE

```bash
sudo zypper install docker docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker $USER   # log out and back in after this
```

Verify:

```bash
docker --version          # Docker version 29.x
docker compose version    # Docker Compose version 5.x
```

#### Installing Docker on other platforms

Follow the [official Docker installation guide](https://docs.docker.com/get-docker/).
Docker Compose v2 is included with Docker Desktop (macOS/Windows) and available as
a plugin on Linux (`docker compose`, not the legacy `docker-compose`).

### Step 1 — Clone the repository

```bash
git clone https://github.com/bzoltan1/download-bugzilla.git
cd download-bugzilla
```

### Step 2 — Configure

```bash
cp .env.example .env
```

No edits are required for basic use. Leave `OLLAMA_BASE_URL` unchanged — the default
`http://ollama:11434` is correct inside the Docker Compose network. Setting it to
`localhost` will break queries.

### Step 3 — Build the application image

This downloads Python dependencies and the MiniLM embedding model (~3.5 GB total).
Only needed once; the image is cached locally after the first build.

```bash
docker compose build
```

### Step 4 — Set up Ollama

Two options depending on whether Ollama is already installed on your system.

#### Option A — Ollama installed as a system service (openSUSE / most Linux)

If you installed Ollama via `sudo zypper install ollama` (or the equivalent for your
distro), it is already running on `localhost:11434`. Pull the model once:

```bash
ollama pull qwen3-coder:30b
```

Then use `app-host` (host-network mode) in all subsequent `docker compose` commands:

```bash
docker compose up -d app-host
```

#### Option B — Ollama managed by Docker Compose

If Ollama is **not** installed on the host, use the bundled sidecar instead:

```bash
docker compose --profile ollama up -d ollama
docker compose exec ollama ollama pull qwen3-coder:30b   # ~18 GB, one-time
```

Then start the app with the sidecar profile:

```bash
docker compose --profile ollama up -d app
```

### Step 5 — Receive the ChromaDB index

Contact the data owner and provide your server's hostname or IP address. They will
rsync the pre-built index directly to your machine (~46 GB):

```bash
# The data owner runs this command on their machine:
rsync -avz --progress \
  /home/balogh/download-bugzilla/chroma_db/ \
  you@your-host:/path/to/download-bugzilla/chroma_db/
```

You can monitor the transfer on your end:

```bash
watch -n5 "du -sh chroma_db/"
```

### Step 6 — Start the application

**Option A (system Ollama):**
```bash
docker compose up -d app-host
docker compose logs -f app-host
```

**Option B (sidecar Ollama):**
```bash
docker compose --profile ollama up -d app
docker compose logs -f app
```

The application starts in seconds. You will see:

```
[entrypoint] Index found (42G) — skipping indexer.
[entrypoint] Starting Flask on 0.0.0.0:5000 ...
```

### Step 7 — Open the web interface

```
http://localhost:5000
```

Or replace `localhost` with your server's hostname if accessing remotely.

---

## Running the demo query script

A non-interactive demo runner is included with five curated showcase queries:

```bash
docker compose exec app python demo_queries.py
```

Or run a single query by number (1–5):

```bash
docker compose exec app python demo_queries.py --queries 1
```

---

## Alternative: build the index from scratch

If you have the raw JSONL file (`bug_reports.jsonl`, ~4 GB) and want to build the
index yourself instead of receiving it via rsync, place the file next to
`docker-compose.yml` and set the path:

```bash
BUGZILLA_JSONL_HOST_PATH=./bug_reports.jsonl docker compose up app
```

The entrypoint detects that `chroma_db/` is empty and starts the indexer automatically.
Indexing 579k bugs takes approximately **15 hours** on a modern CPU.

---

## Running without Docker (bare-metal / venv)

### Requirements

- Python 3.11 or later
- [Ollama](https://ollama.com) with `qwen3-coder:30b` pulled
- ~50 GB free disk

### On openSUSE / SUSE

```bash
sudo zypper install python313 ollama
sudo systemctl enable --now ollama
ollama pull qwen3-coder:30b
```

### Install Python dependencies

```bash
python3 -m venv bugzilla-env
bugzilla-env/bin/pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Edit .env: set OLLAMA_BASE_URL=http://localhost:11434
#            set CHROMA_DIR=chroma_db/
```

### Run

```bash
# Web interface
bugzilla-env/bin/python app.py --db chroma_db/

# Interactive CLI
bugzilla-env/bin/python query_cli.py --db chroma_db/

# Demo queries
bugzilla-env/bin/python demo_queries.py --db chroma_db/
```

---

## Downloading bug data (data owners only)

This section is only relevant if you are building or refreshing the dataset.

### First-time full download

A full download of ~579k bugs takes approximately **8 days** with 29 API keys
rotating on rate limits. Run it in a persistent screen session:

```bash
mkdir -p data logs
screen -dmS bugzilla-download bash -c \
  'bugzilla-env/bin/python download_bugzilla.py \
   --output data/bug_reports.jsonl >> logs/download.log 2>&1'

# Monitor
tail -f logs/download.log
wc -l data/bug_reports.jsonl
```

### Incremental update

Fetch only bugs created or updated since a given date (typically 30–60 minutes):

```bash
bugzilla-env/bin/python download_bugzilla.py \
  --since 2026-05-30 \
  --output data/bug_reports.jsonl
```

### Indexing

```bash
screen -dmS bugzilla-index bash -c \
  'bugzilla-env/bin/python index_bugs_to_chroma.py \
   --input data/bug_reports.jsonl \
   --db chroma_db/ >> logs/index.log 2>&1'

# Monitor
tail -f logs/index.log
ls -lh chroma_db/chroma.sqlite3
```

The indexer is fully resumable — it queries ChromaDB for already-indexed bug IDs at
startup and skips them.

---

## Project structure

```
download-bugzilla/
  app.py                    # Stage 4: Flask web interface
  rag_engine.py             # Shared RAG engine (no side effects on import)
  query_cli.py              # Stage 3: Interactive CLI query interface
  index_bugs_to_chroma.py   # Stage 2: Embed and index bugs into ChromaDB
  download_bugzilla.py      # Stage 1: Download bugs from Bugzilla REST API
  demo_queries.py           # Non-interactive demo runner (5 showcase queries)
  templates/
    index.html              # Flask Jinja2 template
  Dockerfile                # Application image (code only, no data)
  docker-compose.yml        # Orchestrates app + ollama services
  entrypoint.sh             # Container start-up logic (index or serve)
  .env.example              # Configuration template
  requirements.txt          # Pinned Python dependencies
  PLAN.md                   # Full design document and session log
  data/                     # Runtime data (gitignored)
    bug_reports.jsonl       # 579k bugs, 4.0 GB (not in repo)
  chroma_db/                # ChromaDB vector store, 46 GB (not in repo)
  logs/                     # Runtime logs (gitignored)
  bugzilla-env/             # Python virtual environment (gitignored)
```

---

## Configuration reference

All configuration is via `.env` (copy from `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `BUGZILLA_BASE_URL` | `https://bugzilla.suse.com/rest/bug` | Bugzilla REST API base URL |
| `BUGZILLA_API_KEYS` | — | Comma-separated API keys (for downloading only) |
| `BUGZILLA_JSONL` | `data/bug_reports.jsonl` | Path to JSONL bug data |
| `CHROMA_DIR` | `chroma_db/` | ChromaDB directory |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Must match at index and query time |
| `OLLAMA_MODEL` | `qwen3-coder:30b` | LLM model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint (use `http://ollama:11434` in Docker) |
| `OLLAMA_TEMPERATURE` | `0.1` | LLM temperature |
| `RAG_TOP_K` | `3` | Source chunks retrieved per query |
| `INDEX_BATCH_SIZE` | `200` | Bugs per ChromaDB write batch |

---

## Performance reference

Measured on AMD Ryzen 7 PRO 7840U (16 cores, 64 GB RAM), openSUSE Tumbleweed, CPU-only.

| Stage | Dataset | Time | Notes |
|---|---|---|---|
| Download (full) | 579k bugs | ~8 days | 29 API keys rotating on rate limits |
| Download (incremental) | ~6k bugs | ~30 min | `--since DATE`; 7–8 s/page server-side |
| Index (full, v2 chunked) | 579k bugs | ~15 hours | 3–7 bugs/sec; ~3.5M chunks; 46 GB DB |
| Query | 579k bug index | ~40 s | `qwen3-coder:30b`; top-k=3 |

### LLM benchmark

| Model | Size | Quant | Time/query | Quality |
|---|---|---|---|---|
| `llama3:latest` | 8B | Q4_0 | 38.8 s | Correct but terse |
| `qwen2.5:7b` | 7.6B | Q4_K_M | 57.9 s | Well-structured; slowest |
| **`qwen3-coder:30b`** | **30.5B** | **Q4_K_M** | **40.2 s** | **Most thorough; recommended** |

---

## License

GNU General Public License v2. See [LICENSE](LICENSE).
