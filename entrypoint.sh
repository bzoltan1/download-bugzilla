#!/bin/bash
# ============================================================
# Bugzilla RAG — container entrypoint
#
# Start-up logic:
#
#   Case 1 — chroma_db is populated (normal case after rsync):
#     Skip indexing, start the web server immediately.
#
#   Case 2 — chroma_db is empty AND bug_reports.jsonl is mounted:
#     Run the indexer (~15 h), then start the web server.
#
#   Case 3 — chroma_db is empty AND no JSONL:
#     Print a clear message explaining what to do, then exit.
#     (The teammate needs to rsync the DB first.)
# ============================================================
set -euo pipefail

CHROMA_DIR="${CHROMA_DIR:-/data/chroma_db}"
JSONL="${BUGZILLA_JSONL:-/data/bug_reports.jsonl}"
DB_FILE="${CHROMA_DIR}/chroma.sqlite3"

echo "========================================================"
echo " Bugzilla RAG"
echo "========================================================"
echo " CHROMA_DIR : ${CHROMA_DIR}"
echo " JSONL      : ${JSONL}"
echo " OLLAMA_URL : ${OLLAMA_BASE_URL:-http://ollama:11434}"
echo " MODEL      : ${OLLAMA_MODEL:-qwen3-coder:30b}"
echo "========================================================"

mkdir -p "${CHROMA_DIR}"

# ── Case 1: index already exists ─────────────────────────────
if [ -f "${DB_FILE}" ]; then
    SIZE=$(du -sh "${DB_FILE}" 2>/dev/null | cut -f1)
    echo "[entrypoint] Index found (${SIZE}) — skipping indexer."

# ── Case 2: no index, but JSONL is present ───────────────────
elif [ -f "${JSONL}" ]; then
    echo "[entrypoint] No index found. JSONL present at ${JSONL}."
    echo "[entrypoint] Starting indexer — this takes approximately 15 hours."
    echo "[entrypoint] Monitor progress:  docker compose logs -f app"
    echo ""
    python /app/index_bugs_to_chroma.py \
        --input  "${JSONL}" \
        --db     "${CHROMA_DIR}" \
        --batch-size "${INDEX_BATCH_SIZE:-200}"
    echo ""
    echo "[entrypoint] Indexing complete."

# ── Case 3: no index, no JSONL ───────────────────────────────
else
    echo ""
    echo "========================================================"
    echo " ERROR: No ChromaDB index and no JSONL source found."
    echo ""
    echo " You need to rsync the pre-built index to this host:"
    echo ""
    echo "   rsync -avz --progress \\"
    echo "     <data-owner-host>:/path/to/chroma_db/ \\"
    echo "     <host-path-mounted-at-${CHROMA_DIR}>/"
    echo ""
    echo " Then restart the container:"
    echo "   docker compose restart app"
    echo "========================================================"
    echo ""
    exit 1
fi

# ── Start the web server ──────────────────────────────────────
echo "[entrypoint] Starting Flask on 0.0.0.0:5000 ..."
exec python /app/app.py --db "${CHROMA_DIR}" --host 0.0.0.0 --port 5000
