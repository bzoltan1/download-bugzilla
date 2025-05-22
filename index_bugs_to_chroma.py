from langchain.vectorstores import Chroma
from langchain.embeddings import HuggingFaceEmbeddings
from langchain.docstore.document import Document
import json
import os

BUG_FILE = "bugs.json"
CHROMA_DIR = "bug_index"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Load bug reports
with open(BUG_FILE, "r", encoding="utf-8") as f:
    bugs = json.load(f)

# Convert bug entries into documents
def bug_to_text(bug):
    header = (
        f"Bug #{bug['bug_number']}\n"
        f"Title: {bug['title']}\n"
        f"Product: {bug['Product']} | Version: {bug['version']}\n"
        f"Component: {bug['Component']}\n"
        f"Reported: {bug['Reported']}\n"
        f"Status: {bug['Status']}\n\n"
        "Conversation:\n"
    )
    body = "\n".join(f"{c['name']}: {c['text'].strip()}" for c in bug["Comments"] if c["text"].strip())
    return header + body

documents = [Document(page_content=bug_to_text(b)) for b in bugs]

# Create embeddings and vector DB
embedding = HuggingFaceEmbeddings(model_name=EMBED_MODEL)
vectorstore = Chroma.from_documents(documents, embedding, persist_directory=CHROMA_DIR)
vectorstore.persist()

print(f"Stored {len(documents)} bug documents into ChromaDB at {CHROMA_DIR}")
