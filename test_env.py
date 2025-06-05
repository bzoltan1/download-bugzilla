# test_env.py

try:
    import requests
    import json
    import time
    import os
    import warnings
    import tqdm
    from langchain import chains
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_ollama import OllamaLLM
    from langchain.chains import RetrievalQA
    from flask import Flask, request, render_template_string, jsonify
    from query_interface import query_bugzilla
    import markdown2
    import threading
    import time
    from collections import deque
    import logging
    from langchain.docstore.document import Document
    import pickle

    print("All imports succeeded. Your environment is correctly set up!")
except Exception as e:
    print("Import failed:", e)


# --- 2. Embedding Model Check ---

# Suppress LangChain deprecation warnings
warnings.filterwarnings("ignore", category=UserWarning, module="langchain")

try:
    print("Loading embedding model...")
    embedding = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    dummy_embedding = embedding.embed_query("test query")
    print(f"Embedding model OK. Vector length: {len(dummy_embedding)}")
except Exception as e:
    print("Embedding model failed:", e)
    exit(1)

# --- 3. Chroma DB Access Check ---
try:
    print("Checking Chroma vector DB access...")
    persist_dir = "bug_index" if os.path.exists("bug_index") else "test_chroma_db"
    vectorstore = Chroma(persist_directory=persist_dir, embedding_function=embedding)
    retriever = vectorstore.as_retriever()
    results = retriever.invoke("sample test") # should return empty list or results

    print(f"Chroma vectorstore loaded. Found {len(results)} sample matches.")
except Exception as e:
    print("Chroma access failed:", e)
    exit(1)

# --- 4. Ollama LLM Check ---
try:
    print("Checking Ollama LLM (model: mistral)...")
    llm = OllamaLLM(model="mistral", temperature=0.1)
    response = llm.invoke("Say hello in one short sentence.")
    print(f"Ollama responded: {response}")
except Exception as e:
    print("Ollama LLM check failed:", e)
    print("Make sure Ollama is running and the 'mistral' model is available.")
    exit(1)

print("\nEnvironment setup looks good! You're ready to run the main scripts.")
