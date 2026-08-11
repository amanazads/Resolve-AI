import os
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_path = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_path))

from app.config import settings
from app.rag.loader import load_documents
from app.rag.chunker import split_documents
from app.rag.vectorstore import vector_store_manager

def main():
    print("=" * 60)
    print("AI Customer Support System - Vector Database Ingestion")
    print("=" * 60)
    
    kb_dir = settings.KNOWLEDGE_BASE_DIR
    print(f"Loading documents from: {kb_dir}")
    
    documents = load_documents(kb_dir)
    print(f"Loaded {len(documents)} raw document files.")
    
    chunks = split_documents(documents)
    print(f"Split documents into {len(chunks)} text chunks.")
    
    print(f"Storing vector embeddings in ChromaDB at: {settings.CHROMA_PERSIST_DIRECTORY}")
    vector_store_manager.build_from_documents(chunks)
    
    print("\nIngestion Completed Successfully!")

if __name__ == "__main__":
    main()
