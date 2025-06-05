import json
import os
import logging
import time
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain.docstore.document import Document
from tqdm import tqdm
import pickle

# Configuration
JSON_FILE = "bug_reports.json"
CHROMA_DIR = "chroma_db"
CHECKPOINT_FILE = "indexed_bugs_checkpoint.pkl"

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
BATCH_SIZE = 1000

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "rb") as f:
            return pickle.load(f)
    return set()

def save_checkpoint(indexed_ids):
    with open(CHECKPOINT_FILE, "wb") as f:
        pickle.dump(indexed_ids, f)

def bug_to_text(bug):
    header = (
        f"Bug #{bug.get('bug_number')}\n"
        f"Title: {bug.get('title', '')}\n"
        f"Product: {bug.get('Product', '')} | Version: {bug.get('version', '')}\n"
        f"Component: {bug.get('Component', '')}\n"
        f"Reported: {bug.get('Reported', '')}\n"
        f"Status: {bug.get('Status', '')}\n\n"
        "Conversation:\n"
    )
    comments = bug.get("Comments", [])
    body = "\n".join(f"{c.get('name', 'unknown')}: {c.get('text', '').strip()}" for c in comments if c.get("text", "").strip())
    return header + body

def create_documents(bugs):
    documents = []
    for bug in bugs:
        content = bug_to_text(bug).strip()
        bug_id = str(bug.get("bug_number", "unknown"))
        if content:
            documents.append(Document(page_content=content, metadata={"bug_id": bug_id}))
    return documents

def main():
    logging.info("Loading bugs from file...")
    with open(JSON_FILE, "r", encoding="utf-8") as f:
        bugs = json.load(f)
    logging.info(f"Loaded {len(bugs)} bugs.")

    embedding = HuggingFaceEmbeddings(model_name=EMBED_MODEL)
    vectorstore = Chroma(persist_directory=CHROMA_DIR, embedding_function=embedding)

    indexed_ids = load_checkpoint()
    logging.info(f"Loaded checkpoint with {len(indexed_ids)} indexed bug IDs.")

    # Filter out already indexed
    bugs_to_index = [b for b in bugs if str(b.get("bug_number")) not in indexed_ids]

    batches = [bugs_to_index[i:i + BATCH_SIZE] for i in range(0, len(bugs_to_index), BATCH_SIZE)]

    for i, batch in enumerate(tqdm(batches, desc="Indexing batches")):
        documents = create_documents(batch)
        non_empty_docs = [doc for doc in documents if doc.page_content.strip()]
        if not non_empty_docs:
            logging.warning(f"Batch {i + 1}: All documents were empty. Skipping.")
            continue

        try:
            vectorstore.add_documents(non_empty_docs)
            for bug in batch:
                indexed_ids.add(str(bug.get("bug_number")))
            save_checkpoint(indexed_ids)
            logging.info(f"Batch {i + 1}: Indexed {len(non_empty_docs)} bugs. Total indexed: {len(indexed_ids)}")
        except Exception as e:
            logging.error(f"Batch {i + 1}: Failed to index due to error: {e}")

    # Show final DB size
    db_file = os.path.join(CHROMA_DIR, "chroma.sqlite3")
    if os.path.exists(db_file):
        size_mb = os.path.getsize(db_file) / (1024 * 1024)
        logging.info(f"Final Chroma DB size: {size_mb:.2f} MB")

if __name__ == "__main__":
    main()
