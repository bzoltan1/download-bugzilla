#!/usr/bin/env python3
"""
Shared RAG engine module.

Imported by query_cli.py and app.py. Has no side effects on import.
Exposes: query_bugzilla(question) -> dict
"""

import logging
import os
import time

import requests
from dotenv import load_dotenv
from langchain_classic.chains import RetrievalQA
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_ollama import OllamaLLM

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.1"))
CHROMA_DIR = os.getenv("CHROMA_DIR", "chroma_db")
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "3"))


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class OllamaNotAvailableError(RuntimeError):
    pass


class ChromaNotReadyError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Lazy-initialized globals
# ---------------------------------------------------------------------------

_qa_chain = None


def _check_ollama():
    """Raise OllamaNotAvailableError with a helpful message if Ollama is not reachable."""
    try:
        requests.get(OLLAMA_BASE_URL, timeout=3)
    except requests.exceptions.ConnectionError:
        raise OllamaNotAvailableError(
            f"Cannot connect to Ollama at {OLLAMA_BASE_URL}. "
            "Is Ollama running? Try: sudo systemctl start ollama"
        )
    except requests.exceptions.Timeout:
        raise OllamaNotAvailableError(
            f"Ollama at {OLLAMA_BASE_URL} timed out. The service may be overloaded."
        )


def _build_chain(chroma_dir: str | None = None) -> RetrievalQA:
    """Build and return the RetrievalQA chain. Called once on first use."""
    db_dir = chroma_dir or CHROMA_DIR

    if not os.path.exists(db_dir):
        raise ChromaNotReadyError(
            f"ChromaDB directory not found: {db_dir}. "
            "Run the indexer first: bugzilla-index"
        )

    logger.info("Loading embedding model: %s", EMBED_MODEL)
    embedding = HuggingFaceEmbeddings(model_name=EMBED_MODEL)

    logger.info("Loading ChromaDB from: %s", db_dir)
    vectorstore = Chroma(persist_directory=db_dir, embedding_function=embedding)

    _check_ollama()
    logger.info("Connecting to Ollama model: %s at %s", OLLAMA_MODEL, OLLAMA_BASE_URL)
    llm = OllamaLLM(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=OLLAMA_TEMPERATURE,
    )

    chain = RetrievalQA.from_chain_type(
        llm=llm,
        retriever=vectorstore.as_retriever(search_kwargs={"k": RAG_TOP_K}),
        return_source_documents=True,
    )
    logger.info("RAG chain ready (top_k=%d).", RAG_TOP_K)
    return chain


def init(chroma_dir: str | None = None):
    """
    Explicitly initialize the RAG engine. Optional — query_bugzilla() also initializes
    lazily on first call. Call this at application startup to fail fast if Ollama or
    ChromaDB are not available.
    """
    global _qa_chain
    _qa_chain = _build_chain(chroma_dir)


def query_bugzilla(question: str, chroma_dir: str | None = None) -> dict:
    """
    Run a RAG query against the Bugzilla vector store.

    Args:
        question: Natural-language question about Bugzilla data.
        chroma_dir: Override ChromaDB path (optional).

    Returns:
        {
            "answer": str,
            "source_documents": list[Document],
            "elapsed_time": float,   # seconds
        }

    Raises:
        OllamaNotAvailableError: If Ollama daemon is not reachable.
        ChromaNotReadyError: If the ChromaDB directory does not exist.
    """
    global _qa_chain
    if _qa_chain is None:
        _qa_chain = _build_chain(chroma_dir)

    start = time.time()
    result = _qa_chain.invoke({"query": question})
    elapsed = time.time() - start

    return {
        "answer": result.get("result", ""),
        "source_documents": result.get("source_documents", []),
        "elapsed_time": elapsed,
    }
