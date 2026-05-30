"""
Stage 4: Flask web interface for the Bugzilla RAG system.

Usage:
    python app.py [--db DIR] [--port N] [--host ADDR]
    bugzilla-serve [--db DIR] [--port N] [--host ADDR]

Routes:
    GET  /        Query form
    POST /        Run RAG query, render results
    GET  /status  JSON: {"processing": N}
    GET  /eta     JSON: {"eta": seconds}
"""

import argparse
import logging
import os
import threading
import time
from collections import deque

import markdown2
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

import rag_engine

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_CHROMA_DIR = os.getenv("CHROMA_DIR", "chroma_db")
BUGZILLA_BASE_URL  = os.getenv("BUGZILLA_BASE_URL", "https://bugzilla.suse.com/rest/bug")
# Derive the bug viewer URL from the base API URL
_base = BUGZILLA_BASE_URL.replace("/rest/bug", "")
BUGZILLA_BUG_URL   = f"{_base}/show_bug.cgi?id="

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Concurrency tracking
# ---------------------------------------------------------------------------
_lock              = threading.Lock()
_processing        = 0          # in-flight request count
_durations: deque  = deque(maxlen=50)   # recent request durations (seconds)


def _bug_link(bug_id: str) -> str:
    return f"{BUGZILLA_BUG_URL}{bug_id}" if bug_id else ""


def _format_sources(source_docs) -> list[dict]:
    """Convert LangChain Document objects into template-friendly dicts."""
    out = []
    for doc in source_docs:
        meta    = doc.metadata or {}
        bug_id  = str(meta.get("bug_id", ""))
        title   = str(meta.get("title", ""))[:120]
        product = meta.get("product", "")
        status  = meta.get("status", "")
        resolution = meta.get("resolution", "")

        parts = [p for p in [product, status, resolution] if p]
        meta_line = " | ".join(parts)

        # Show a clean snippet: strip the structured header, show body text
        body = doc.page_content
        # The header ends at the first blank line after "Bug #..."
        if "\n\n" in body:
            snippet = body[body.index("\n\n") + 2:].strip()
        else:
            snippet = body.strip()
        snippet = snippet[:800]

        out.append({
            "bug_id":    bug_id,
            "bug_link":  _bug_link(bug_id),
            "title":     title,
            "meta_line": meta_line,
            "snippet":   snippet,
        })
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def index():
    global _processing

    question = ""
    answer   = ""
    sources  = []
    elapsed  = ""
    error    = ""

    if request.method == "POST":
        question = request.form.get("question", "").strip()
        if question:
            with _lock:
                _processing += 1
            t0 = time.time()
            try:
                result  = rag_engine.query_bugzilla(question)
                answer  = markdown2.markdown(result["answer"])
                sources = _format_sources(result.get("source_documents", []))
                elapsed = f"{result['elapsed_time']:.1f}"
            except rag_engine.OllamaNotAvailableError as e:
                error = str(e)
            except rag_engine.ChromaNotReadyError as e:
                error = str(e)
            except Exception as e:
                logger.exception("Unexpected error during query")
                error = f"Unexpected error: {e}"
            finally:
                duration = time.time() - t0
                with _lock:
                    _processing -= 1
                    _durations.append(duration)
                if not elapsed:
                    elapsed = f"{duration:.1f}"

    return render_template(
        "index.html",
        question=question,
        answer=answer,
        sources=sources,
        elapsed=elapsed,
        error=error,
    )


@app.route("/status")
def status():
    with _lock:
        return jsonify({"processing": _processing})


@app.route("/eta")
def eta():
    with _lock:
        avg = (sum(_durations) / len(_durations)) if _durations else 40.0
        estimate = round(_processing * avg, 1)
    return jsonify({"eta": estimate})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Bugzilla RAG web interface."
    )
    parser.add_argument("--db",   default=DEFAULT_CHROMA_DIR, metavar="DIR",
                        help=f"ChromaDB directory (default: {DEFAULT_CHROMA_DIR})")
    parser.add_argument("--port", type=int, default=5000, metavar="N",
                        help="HTTP port (default: 5000)")
    parser.add_argument("--host", default="0.0.0.0", metavar="ADDR",
                        help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Override chroma dir if passed on CLI
    if args.db != DEFAULT_CHROMA_DIR:
        os.environ["CHROMA_DIR"] = args.db

    logger.info("Starting Bugzilla RAG web interface on %s:%d", args.host, args.port)
    logger.info("ChromaDB: %s", args.db)

    # Eagerly initialise the RAG engine so startup errors surface immediately
    try:
        rag_engine.init(chroma_dir=args.db)
    except (rag_engine.OllamaNotAvailableError, rag_engine.ChromaNotReadyError) as e:
        logger.error("Startup failed: %s", e)
        raise SystemExit(1)

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
