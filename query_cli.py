#!/usr/bin/env python3
"""
Stage 3: Interactive CLI for querying the Bugzilla RAG system.

Usage:
    bugzilla-query [--db DIR] [--top-k N] [--verbose]
    python query_cli.py [--db DIR] [--top-k N] [--verbose]
"""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def get_multiline_input(prompt: str = "") -> str | None:
    """Read multi-line input terminated by '###' on its own line. Returns None on EOF."""
    if prompt:
        print(prompt)
    lines = []
    while True:
        try:
            line = input()
            if line.strip() == "###":
                break
            lines.append(line)
        except EOFError:
            if not lines:
                return None
            break
    return "\n".join(lines).strip() or None


def run_cli(chroma_dir: str | None = None):
    """Run the interactive query loop."""
    from rag_engine import OllamaNotAvailableError, ChromaNotReadyError, init, query_bugzilla

    try:
        init(chroma_dir=chroma_dir)
    except (OllamaNotAvailableError, ChromaNotReadyError) as e:
        logger.error("%s", e)
        sys.exit(1)

    print("Bugzilla RAG — type your question, end with '###' on a new line. Ctrl+C to exit.")
    print()

    while True:
        try:
            query = get_multiline_input("Question:")
            if query is None:
                # EOF — exit cleanly
                print("\nGoodbye.")
                break
            if not query:
                continue

            print("Processing...")
            result = query_bugzilla(query, chroma_dir=chroma_dir)

            print(f"\nAnswer (in {result['elapsed_time']:.1f}s):\n{result['answer']}")
            print("\nSources:")
            for i, doc in enumerate(result["source_documents"], 1):
                bug_id = doc.metadata.get("bug_id", "?")
                title = doc.metadata.get("title", "")[:80]
                snippet = doc.page_content[:500].replace("\n", " ")
                print(f"  [{i}] Bug #{bug_id}: {title}")
                print(f"       {snippet}...")
            print()

        except KeyboardInterrupt:
            print("\nGoodbye.")
            break


def main():
    parser = argparse.ArgumentParser(
        description="Interactive CLI query interface for the Bugzilla RAG system."
    )
    parser.add_argument(
        "--db", default=os.getenv("CHROMA_DIR", "chroma_db"), metavar="DIR",
        help="ChromaDB directory (default: from .env or chroma_db/)"
    )
    parser.add_argument(
        "--top-k", type=int, default=None, metavar="N",
        help="Number of source documents to retrieve (overrides .env RAG_TOP_K)"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable DEBUG logging"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Allow --top-k to override the env var before rag_engine imports it
    if args.top_k is not None:
        os.environ["RAG_TOP_K"] = str(args.top_k)

    run_cli(chroma_dir=args.db)


if __name__ == "__main__":
    main()
