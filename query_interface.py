import time
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_ollama import OllamaLLM
from langchain.chains import RetrievalQA

# Load vector DB and embedding model
embedding = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
vectorstore = Chroma(persist_directory="bug_index", embedding_function=embedding)

# Set up local model via Ollama
llm = OllamaLLM(model="mistral", temperature=0.1)

# RAG chain: retrieve top 3 relevant bug reports, feed to model
qa_chain = RetrievalQA.from_chain_type(
    llm=llm,
    retriever=vectorstore.as_retriever(search_kwargs={"k": 3}),
    return_source_documents=True
)

# CLI interface
print("🔍 Ask a question about your Bugzilla data (Ctrl+C to exit):")
while True:
    try:
        query = input("\nPrompt: ")
        if not query.strip():
            continue

        start_time = time.time()
        result = qa_chain.invoke({"query": query})
        end_time = time.time()
        elapsed_time = end_time - start_time

        print(f"\nAssistant (responded in {elapsed_time:.2f} seconds):\n", result["result"])
        print("\nTop matching bug records:")
        for i, doc in enumerate(result["source_documents"], 1):
            print(f"\n--- Match {i} ---\n", doc.page_content[:1000], "...\n")
    except KeyboardInterrupt:
        print("\nGoodbye!")
        break
