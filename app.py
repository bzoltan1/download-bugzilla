from flask import Flask, request, render_template_string, jsonify
from query_interface import query_bugzilla
import markdown2
import threading
import time
from collections import deque

app = Flask(__name__)

# Track processing requests and durations
processing_requests = 0
request_durations = deque(maxlen=50)
lock = threading.Lock()

TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Bugzilla RAG Query</title>
  <style>
    body { font-family: sans-serif; max-width: 800px; margin: auto; padding: 2em; }
    .response { background: #f9f9f9; padding: 1em; border-radius: 8px; margin-top: 1em; }
    .source-snippet { margin-top: 1em; padding: 1em; background: #fafafa; border: 1px solid #ccc; border-radius: 6px; }
    textarea { width: 100%; font-size: 1em; padding: 0.5em; }
    button { padding: 0.5em 1em; font-size: 1em; margin-top: 0.5em; }
    #loading { display: none; color: #555; font-style: italic; margin-top: 1em; }
    .notice { font-size: 0.9em; color: #444; margin-bottom: 1em; background: #eef; padding: 1em; border-radius: 6px; }
  </style>
  <script>
    function showLoading() {
      document.getElementById("loading").style.display = "block";
      document.getElementById("ask-btn").disabled = true;
    }

    async function updateStatus() {
      const res = await fetch('/status');
      const data = await res.json();
      document.getElementById("status").innerText =
        `${data.processing} request(s) in progress`;

      const etaRes = await fetch('/eta');
      const eta = await etaRes.json();
      document.getElementById("eta").innerText =
        `Estimated wait time: ~${eta.eta} seconds`;
    }

    window.onload = updateStatus;
  </script>
</head>
<body>
  <h1>🔍 Ask Bugzilla</h1>
  <div class="info-box">
    <strong>This app is to demonstrate this <a href="https://bzoltan1.github.io/building-a-local-bugzilla-rag-system/" target="_blank">blog post</a>.</strong><br>
    <p>
    This is a simple interface to ask questions against a large Bugzilla dataset using Retrieval-Augmented Generation (RAG).
    </p>
    <p>
    When a question is submitted, the backend queries a local ChromaDB vector store containing 500,000 Bugzilla records.
    </p>
    <p>
    It uses an efficient sentence embedding model (<code>all-MiniLM-L6-v2</code>) to find relevant documents based on semantic similarity, then passes those to a language model (like LLaMA via Ollama) to generate a context-aware answer.
    </p>
    The system tracks how many queries are being processed in real time and tries to estimates the expected wait time using a sliding window of recent request durations.
    This helps inform users about server load before they submit their question.
    The entire pipeline runs on VM with limited resources, proving that large-scale semantic search and generative QA are possible without cloud GPUs.
    <p>
    What makes it cool is that it brings advanced AI-powered support search to legacy systems like Bugzilla using open-source tools, a local database, and a small, fast language model—while keeping the user informed and the backend lean.
    </p>
    <p>
    </p>

  </div>


  <div id="status">Checking current load...</div>
  <div id="eta"></div>

  <form method="post" onsubmit="showLoading()">
    <textarea name="question" rows="4" placeholder="Enter your question here...">{{ question }}</textarea><br>
    <button id="ask-btn" type="submit">Ask</button>
  </form>

  <div id="loading">⏳ Processing your question...</div>

  {% if answer %}
  <h2>✅ Answer (in {{ time }} seconds):</h2>
  <div class="response">{{ answer|safe }}</div>

  <h3>📄 Top Source Snippets:</h3>
  {% for doc in sources %}
    <div class="source-snippet">
      {% if doc.bug_link %}
        <strong>Bug ID: <a href="{{ doc.bug_link }}" target="_blank">{{ doc.bug_id }}</a></strong><br>
      {% endif %}
      <pre>{{ doc.content }}</pre>
    </div>
  {% endfor %}
  {% endif %}
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def index():
    question = ""
    html_answer = ""
    sources = []
    time_taken = ""

    if request.method == "POST":
        question = request.form["question"]
        if question.strip():
            start_time = time.time()
            with lock:
                global processing_requests
                processing_requests += 1
            try:
                result = query_bugzilla(question)
                html_answer = markdown2.markdown(result["result"])
                sources = []
                for doc in result["source_documents"]:
                    content = doc.page_content[:1000]
                    bug_id = doc.metadata.get("bug_id") if doc.metadata else None
                    bug_link = f"https://bugzilla.suse.com/show_bug.cgi?id={bug_id}" if bug_id else None
                    sources.append({"content": content, "bug_id": bug_id, "bug_link": bug_link})
                elapsed = result["elapsed_time"]
            except Exception as e:
                html_answer = f"<div style='color:red;'>Error: {str(e)}</div>"
                elapsed = time.time() - start_time
            finally:
                with lock:
                    processing_requests -= 1
                    request_durations.append(elapsed)
                time_taken = f"{elapsed:.2f}"

    return render_template_string(
        TEMPLATE,
        question=question,
        answer=html_answer,
        sources=sources,
        time=time_taken
    )

@app.route("/status")
def status():
    with lock:
        return jsonify({"processing": processing_requests})

@app.route("/eta")
def eta():
    with lock:
        if request_durations:
            avg = sum(request_durations) / len(request_durations)
        else:
            avg = 6.0  # fallback estimate in seconds
        estimate = processing_requests * avg
    return jsonify({"eta": round(estimate, 1)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
