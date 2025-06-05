
# Overview of the Bugzilla RAG Query Web Interface - app.py
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

