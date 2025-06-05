# Bugzilla RAG System – Flowchart

```mermaid
graph TD

A[setup_env.sh: Setup Environment] --> B[download_bugzilla.py: Fetch Bug Reports & Comments]
B --> C[bug_reports.json: Raw JSON File]
C --> D[index_bugs_to_chroma.py: Index to ChromaDB]
D --> E[chroma_db: Vector Store]

subgraph Optional Testing
    A --> T[test_env.py: Environment Test]
end

E --> F1[query_interface.py: CLI RAG Interface]
E --> F2[app.py: Web RAG Interface]

%% CLI RAG Query Flow
F1 --> G1[User Inputs Question]
G1 --> H1[Search ChromaDB (Semantic Search)]
H1 --> I1[Retrieve Top Documents]
I1 --> J1[Generate Answer with Local Mistral LLM]
J1 --> K1[Display Answer and Source Snippets]

%% Web RAG Query Flow
F2 --> G2[User Inputs via Web UI]
G2 --> H2[Search ChromaDB (Semantic Search)]
H2 --> I2[Retrieve Top Documents]
I2 --> J2[Generate Answer with Local Mistral LLM]
J2 --> K2[Display Answer and Source Snippets]



# `download_bugzilla.py` - Bugzilla Bug and Comment Fetcher
Fetches bug reports and their associated comments from a Bugzilla REST API. It supports:
- API key rotation
- Rate limit handling
- Incremental data saving
- Graceful recovery from network errors or corrupt output


## Configuration

- **API Endpoint**
  - Bug list: `https://<host>/rest/bug`
  - Comments: `https://<host>/rest/bug/<bug_id>/comment`

- **API Keys**
  - `api_keys`: List of API keys used in a round-robin strategy
  - On `429 Too Many Requests`, `503`, or timeout: rotates to the next key

- **Fetch Parameters**
  - `limit=500`
  - `offset` calculated from length of previously fetched data

- **Output File**
  - `bug_reports.json`
  - Corrupt JSON triggers automatic renaming to `.corrupt_backup`


## Functionality

- **Bug Fetching**
  - Calls `/rest/bug` with paging support (`limit`, `offset`)
  - Extracts: `id`, `summary`, `product`, `version`, `component`, `creation_time`, `status`

- **Comment Fetching**
  - For each bug, calls `/rest/bug/<id>/comment`
  - Extracts: `creator`, `creation_time`, `text`
  - Structured into a `"Comments"` array per bug

- **Data Persistence**
  - Data saved to `bug_reports.json`
  - Autosaves after every 500 new bugs
  - Final save on completion or keyboard interrupt

- **Resumability**
  - Loads and resumes from existing `bug_reports.json` using length for offset

## Error Handling

- **JSON Parsing Errors**
  - Backs up corrupt `bug_reports.json` to `bug_reports.json.corrupt_backup`

- **HTTP Errors**
  - `429`: rotate key, sleep 60s
  - `503`: rotate key, sleep 30s
  - Other HTTP errors: log and skip

- **Timeouts / Network Errors**
  - Rotate key, exponential backoff (max 12 hours), retries indefinitely

- **KeyboardInterrupt**
  - Saves progress and exits cleanly

# `index_bugs_to_chroma.py` - Bugzilla indexing script for chroma vector store
This script processes a JSON file of Bugzilla bugs and indexes their content into a **Chroma** vector database using **sentence-transformer embeddings**. It supports **checkpointing** to allow resumption after interruptions and processes documents in batches to manage memory and API limits.

## Key Features
- Reads structured bug data from a JSON file.
- Converts bug entries (metadata + comments) into text documents.
- Uses HuggingFace's MiniLM embedding model.
- Stores embeddings in Chroma DB with persistence.
- Implements batching and checkpointing for fault-tolerant processing.
- Logs progress, warnings, and errors throughout indexing.

## Configuration

| Parameter          | Value / Default                                 |
|--------------------|--------------------------------------------------|
| `JSON_FILE`        | `"bug_reports.json"`                             |
| `CHROMA_DIR`       | `"chroma_db"`                                    |
| `CHECKPOINT_FILE`  | `"indexed_bugs_checkpoint.pkl"`                  |
| `EMBED_MODEL`      | `"sentence-transformers/all-MiniLM-L6-v2"`       |
| `BATCH_SIZE`       | `1000`                                           |

## Workflow Description

### 1. Loading Bugs
- Reads the full list of bugs from a JSON file into memory.
- Each bug is expected to have fields like `bug_number`, `title`, `Product`, `version`, `Component`, `Status`, `Reported`, and `Comments`.

### 2. Checkpointing
- Keeps track of indexed bug IDs using a pickle file (`indexed_bugs_checkpoint.pkl`).
- Prevents reprocessing already-indexed documents across runs.

### 3. Bug-to-Text Conversion
- `bug_to_text(bug)` creates a human-readable string from bug metadata and comment threads.
- Skips bugs with empty content after formatting.

### 4. Document Creation
- `create_documents()` builds a list of `Document` objects containing formatted text and `bug_id` metadata.

### 5. Vector Store and Embedding
- Initializes `HuggingFaceEmbeddings` with MiniLM model.
- Creates or loads a Chroma vector store from the `chroma_db` directory.
- Documents are embedded and added in batches to the vector store.

### 6. Indexing Loop
- Batches are created from the filtered list of unindexed bugs.
- For each batch:
  - Converts to documents.
  - Filters out empty documents.
  - Adds them to the vector store.
  - Updates the checkpoint file with newly indexed bug IDs.

### 7. Final DB Size
- Logs the final size of the `chroma.sqlite3` database (if it exists).

## Logging and Progress
- Uses Python’s `logging` module for clear timestamps and log levels.
- Uses `tqdm` progress bars for batch indexing visualization.

## Dependencies
- `langchain_huggingface`
- `langchain_chroma`
- `tqdm`
- `pickle`
- `json`
- `os`
- `logging`
- `time`

## Usage
1. Place your Bugzilla data in a file called `bugs.json`.
2. Make a huge portion of hot bewerage and be prepared to wait forever
3. Run the script in a terminal
   ```bash
   python index_bugs_to_chroma.py
   ```



# `query_interface.py` Bugzilla RAG Query CLI Tool
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


# `app.py` - Bugzilla RAG Query Web Interface
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

