# ============================================================
# Bugzilla RAG — application image
#
# This image contains ONLY the application code and its
# Python dependencies (including the MiniLM embedding model).
# No bug data is baked in.
#
# Data is provided at runtime via bind mounts (see docker-compose.yml):
#   /data/chroma_db      — ChromaDB vector index (46 GB)
#                          Populated by rsyncing from the original machine.
#   /data/bug_reports.jsonl — Raw JSONL (4 GB, optional)
#                          Only needed if you want to re-index from scratch.
#
# Normal workflow for a new teammate:
#   1. docker compose build
#   2. docker compose up -d ollama
#   3. docker compose exec ollama ollama pull qwen3-coder:30b
#   4. Contact the data owner to rsync chroma_db/ to this host
#   5. docker compose up -d app
#   6. Open http://localhost:5000
# ============================================================

FROM python:3.13-slim

# ── system deps ──────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── application directory ────────────────────────────────────
WORKDIR /app

# ── Python dependencies (pinned) ─────────────────────────────
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ── Pre-download the embedding model ─────────────────────────
# Bake the MiniLM weights into the image so the container works
# fully offline at runtime — no HuggingFace access needed.
RUN python - <<'EOF'
from sentence_transformers import SentenceTransformer
SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
print("Embedding model cached.")
EOF

# ── Application source ────────────────────────────────────────
COPY app.py \
     rag_engine.py \
     query_cli.py \
     index_bugs_to_chroma.py \
     download_bugzilla.py \
     demo_queries.py \
     ./
COPY templates/ templates/

# ── Runtime configuration defaults ───────────────────────────
ENV CHROMA_DIR=/data/chroma_db \
    BUGZILLA_JSONL=/data/bug_reports.jsonl \
    EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2 \
    OLLAMA_MODEL=qwen3-coder:30b \
    OLLAMA_BASE_URL=http://ollama:11434 \
    OLLAMA_TEMPERATURE=0.1 \
    RAG_TOP_K=3 \
    INDEX_BATCH_SIZE=200 \
    PYTHONUNBUFFERED=1

# Both data paths are provided at runtime — not in the image.
VOLUME ["/data/chroma_db"]

EXPOSE 5000

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
