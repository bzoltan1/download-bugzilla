import time
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_ollama import OllamaLLM
from langchain.chains import RetrievalQA

# Load vector DB and embedding model
embedding = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
vectorstore = Chroma(persist_directory="chroma_db", embedding_function=embedding)

# Set up local model via Ollama
llm = OllamaLLM(model="mistral", temperature=0.1)

# RAG chain
qa_chain = RetrievalQA.from_chain_type(
    llm=llm,
    retriever=vectorstore.as_retriever(search_kwargs={"k": 3}),
    return_source_documents=True
)

def get_multiline_input(prompt="Enter your prompt (end with '###' on a new line):\n"):
    print(prompt)
    lines = []
    while True:
        try:
            line = input()
            if line.strip() == "###":
                break
            lines.append(line)
        except EOFError:
            break
    return "\n".join(lines).strip()

# CLI
print("🔍 Ask a question about your Bugzilla data. Type '###' on a new line to finish your prompt (Ctrl+C to exit).")
while True:
    try:
        query = get_multiline_input()
        if not query:
            continue

        print("⏳ Processing your question, please wait...")

        start_time = time.time()
        result = qa_chain.invoke({"query": query})
        elapsed_time = time.time() - start_time

        print(f"\n✅ Assistant (responded in {elapsed_time:.2f} seconds):\n{result['result']}")
        print("\n📄 Top matching bug records:")
        for i, doc in enumerate(result["source_documents"], 1):
            print(f"\n--- Match {i} ---\n{doc.page_content[:1000]}...\n")
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
        break
