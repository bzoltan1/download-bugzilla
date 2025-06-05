# Bugzilla RAG Query CLI Tool - query_interface.py
This script provides a **command-line interface (CLI)** to query a local Bugzilla dataset using a **Retrieval-Augmented Generation (RAG)** approach. It integrates a local vector database with a language model to answer user questions by retrieving and generating context-aware responses based on Bugzilla records.

## Key Features
- **Multiline User Input**: Supports multi-line questions terminated by typing `###` on a new line.
- **Semantic Search**: Uses a vector store with sentence embeddings to find relevant Bugzilla documents.
- **Local Language Model**: Generates answers locally using the Ollama-hosted `mistral` model.
- **Source Document Display**: Prints top retrieved source snippets alongside the generated answer.
- **Simple CLI loop** with graceful keyboard interrupt handling.

## Architecture Components

### 1. Vector Store and Embeddings
- Uses **HuggingFaceEmbeddings** with model `"sentence-transformers/all-MiniLM-L6-v2"` for converting queries and documents into embeddings.
- **Chroma** vector store loads a persisted index from `chroma_db` directory.
- Vector store retrieval fetches the top 3 (`k=3`) most relevant documents.

### 2. Language Model
- Connects to local LLM via **OllamaLLM**, specifically the `"mistral"` model.
- Temperature parameter is set to `0.1` to generate consistent and less random responses.

### 3. RetrievalQA Chain
- Combines retriever and LLM to form a RAG chain.
- Configured to return both generated answers and source documents.

### 4. User Input Handling
- `get_multiline_input` function:
  - Prompts user to enter multi-line queries.
  - Input ends when user types `###` on a new line or EOF is reached.
- Prints processing status while querying.

### 5. Output
- Prints the generated answer with elapsed time.
- Lists the top 3 matching Bugzilla document snippets (truncated to 1000 chars).

### 6. Control Flow
- Runs an infinite loop prompting user input.
- Handles `KeyboardInterrupt` (Ctrl+C) to exit cleanly.

## Dependencies
- `langchain_chroma` — Chroma vector DB integration.
- `langchain_huggingface` — HuggingFace sentence embeddings.
- `langchain_ollama` — Ollama local LLM wrapper.
- Standard Python libraries: `time`, `input`.

## Usage
1. Run the script in a terminal.
2. Type your multi-line question.
3. End input with `###`.
4. Wait for the response and source snippets.
5. Repeat or press Ctrl+C to exit.

## Deployment Notes
- Requires:
  - Prebuilt Chroma vector store at `chroma_db`.
  - Local Ollama server running with the `mistral` model available.
- Suitable for local, resource-limited environments without cloud dependency.


# Bugzilla RAG Query Web Interface - app.py
This script implements a lightweight Flask web application that serves as a front-end for querying a large, local Bugzilla dataset using a **Retrieval-Augmented Generation (RAG)** pipeline. It enables users to ask natural language questions and receive contextually grounded answers retrieved from a ChromaDB vector store and processed by a local language model.

## Key Features
- **Web Interface** for inputting user questions and displaying answers with sources.
- **RAG Pipeline Integration** using:
  - **`all-MiniLM-L6-v2`** for sentence embeddings.
  - **ChromaDB** as the vector store.
  - A language model backend (e.g., **LLaMA via Ollama**) for answer generation.
- **Real-Time Server Load Tracking**:
  - Concurrent request count.
  - Estimated wait time computed from a rolling window of recent processing durations.

## Architecture Components

### 1. Frontend
- HTML rendered via `render_template_string()` with inline CSS and JS.
- Functional Elements:
  - Input: Textarea for user questions.
  - Output: Rendered Markdown answer, source snippets, system status.
  - JavaScript fetches `/status` and `/eta` endpoints to show:
    - Active query count.
    - Estimated wait time.

### 2. Backend
- **Flask Web Server**
  - Routes:
    - `/`: Handles GET (page load) and POST (query submission).
    - `/status`: Returns current number of processing requests.
    - `/eta`: Calculates ETA based on average processing times from a `deque`.

- **Thread-Safe Request Management**
  - Global counter `processing_requests` tracks concurrent queries.
  - `deque` (maxlen=50) records recent durations for ETA.
  - `threading.Lock` ensures safe access to shared state.

- **Bugzilla Query Handling**
  - `query_bugzilla(question)` is called to:
    - Retrieve semantically relevant documents.
    - Generate an answer.
  - Markdown is rendered to HTML via `markdown2.markdown()`.

### 3. Dependencies
- Python modules:
  - `flask`
  - `markdown2`
  - `threading`,
  - `time`,
  - `collections.deque`
  - Custom module: `query_interface`

## Deployment
- Runs on `0.0.0.0:5000` with `debug=True` for development.
- Requires local setup of:
  - ChromaDB with pre-indexed Bugzilla documents.
  - LLM inference engine (e.g., Ollama running LLaMA or similar).

## Usage Example
A user enters:  
> "How was bug 123456 resolved?"  
The system retrieves semantically related documents, generates an answer using the language model, and presents it alongside links to the relevant Bugzilla records.

