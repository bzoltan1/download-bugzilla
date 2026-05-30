#!/usr/bin/env python3
"""
Bugzilla RAG — Demo query runner.

Runs a curated set of showcase queries against the production ChromaDB index
and prints formatted results to stdout.  No interactivity required — suitable
for live demos, CI smoke tests, or recording a terminal session.

Usage:
    bugzilla-env/bin/python demo_queries.py [--db DIR] [--top-k N] [--verbose]

Each query is printed with a header, the answer, elapsed time, and the top
source bug links.  A summary table is printed at the end.
"""

import argparse
import os
import sys
import textwrap
import time
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Showcase queries
# ---------------------------------------------------------------------------
# Chosen to demonstrate breadth:
#   1. CVE / security — the most common production use-case
#   2. Kernel subsystem — dense, long comment threads
#   3. Resolution / WONTFIX — tests the resolution chunk strategy
#   4. Container / Docker — shows multi-version historical coverage
#   5. Cross-component — NetworkManager + wicked + YaST interaction

QUERIES = [
    {
        "title": "Docker CVE history",
        "question": (
            "What Docker CVEs were reported between 2014 and 2016 "
            "and how were they resolved?"
        ),
    },
    {
        "title": "btrfs kernel hangs",
        "question": (
            "Are there open or unresolved btrfs hang or deadlock bugs "
            "in the kernel component?"
        ),
    },
    {
        "title": "Kernel privilege escalation CVEs",
        "question": (
            "Which kernel privilege escalation CVEs were fixed in "
            "SUSE Linux Enterprise and what patches were applied?"
        ),
    },
    {
        "title": "NetworkManager / wicked regression",
        "question": (
            "Are there bugs about NetworkManager or wicked breaking "
            "network connectivity after an upgrade?"
        ),
    },
    {
        "title": "SLE 15 SP4 kernel CVEs",
        "question": (
            "What kernel CVE bugs were tracked for SLE 15 SP4 "
            "and what is their current status?"
        ),
    },
]

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

SEPARATOR = "=" * 72
THIN_SEP  = "-" * 72


def _header(n: int, title: str) -> str:
    return f"\n{SEPARATOR}\n  Query {n}/{len(QUERIES)}: {title}\n{SEPARATOR}"


def _wrap(text: str, width: int = 70, indent: str = "  ") -> str:
    return textwrap.fill(text, width=width, initial_indent=indent,
                         subsequent_indent=indent)


def _print_result(n: int, query: dict, result: dict, bugzilla_base: str):
    print(_header(n, query["title"]))
    print()
    print(_wrap(f"Q: {query['question']}"))
    print()

    answer = result["answer"].strip()
    # Strip any <think>...</think> tags from reasoning models
    import re
    answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()

    print("  Answer:")
    for line in answer.splitlines():
        print(_wrap(line) if line.strip() else "")
    print()
    print(f"  Time: {result['elapsed_time']:.1f}s")
    print()

    docs = result.get("source_documents", [])
    if docs:
        print(f"  Sources ({len(docs)} retrieved):")
        for doc in docs:
            meta   = doc.metadata or {}
            bug_id = meta.get("bug_id", "?")
            title  = str(meta.get("title", ""))[:80]
            status = meta.get("status", "")
            res    = meta.get("resolution", "")
            state  = f"{status}/{res}" if res else status
            link   = f"{bugzilla_base}{bug_id}"
            print(f"    [{bug_id}] {title}")
            print(f"           {state}  {link}")
    print()


def _print_summary(results: list[dict]):
    print(f"\n{SEPARATOR}")
    print("  Summary")
    print(SEPARATOR)
    total = sum(r["elapsed"] for r in results)
    for r in results:
        status = "OK" if r["ok"] else "ERR"
        print(f"  [{status}] {r['title']:<38}  {r['elapsed']:5.1f}s")
    print(THIN_SEP)
    print(f"  Total elapsed: {total:.1f}s   ({len(results)} queries)\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Run Bugzilla RAG demo queries and print formatted results."
    )
    parser.add_argument("--db", default=os.getenv("CHROMA_DIR", "chroma_db"),
                        metavar="DIR", help="ChromaDB directory")
    parser.add_argument("--top-k", type=int, default=None, metavar="N",
                        help="Number of source documents to retrieve per query")
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    parser.add_argument("--queries", metavar="N", type=int, nargs="*",
                        help="Run only specific query numbers (1-based)")
    args = parser.parse_args()

    import logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.top_k:
        os.environ["RAG_TOP_K"] = str(args.top_k)

    import rag_engine

    bugzilla_base = os.getenv("BUGZILLA_BASE_URL", "https://bugzilla.suse.com/rest/bug")
    bugzilla_base = bugzilla_base.replace("/rest/bug", "") + "/show_bug.cgi?id="

    print(f"\n{SEPARATOR}")
    print("  Bugzilla RAG — Demo")
    print(THIN_SEP)
    print(f"  ChromaDB : {args.db}")
    print(f"  Model    : {os.getenv('OLLAMA_MODEL', 'qwen3-coder:30b')}")
    print(f"  Top-K    : {os.getenv('RAG_TOP_K', '3')}")
    print(f"{SEPARATOR}\n")

    print("  Initialising RAG engine (loading embeddings + connecting to Ollama)...")
    try:
        rag_engine.init(chroma_dir=args.db)
    except (rag_engine.OllamaNotAvailableError, rag_engine.ChromaNotReadyError) as e:
        print(f"\n  ERROR: {e}\n")
        sys.exit(1)
    print("  Ready.\n")

    selected = QUERIES
    if args.queries:
        selected = [QUERIES[i - 1] for i in args.queries if 1 <= i <= len(QUERIES)]

    summary = []
    for n, query in enumerate(selected, 1):
        t0 = time.time()
        ok = True
        result = None
        try:
            result = rag_engine.query_bugzilla(query["question"])
            _print_result(n, query, result, bugzilla_base)
        except Exception as e:
            ok = False
            print(_header(n, query["title"]))
            print(f"\n  ERROR: {e}\n")
        elapsed = time.time() - t0
        summary.append({
            "title":   query["title"],
            "ok":      ok,
            "elapsed": result["elapsed_time"] if result else elapsed,
        })

    _print_summary(summary)


if __name__ == "__main__":
    main()
