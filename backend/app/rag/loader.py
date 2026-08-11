import os
from pathlib import Path
from typing import List, Dict, Any
from langchain_core.documents import Document

def load_documents(directory_path: str) -> List[Document]:
    """
    Recursively scans the directory and loads all .md and .txt files.
    Attaches metadata (document_name, category, source, relative path).
    """
    documents = []
    base_path = Path(directory_path)
    
    if not base_path.exists():
        raise FileNotFoundError(f"Knowledge base directory not found at: {directory_path}")

    for file_path in base_path.rglob("*"):
        if file_path.is_file() and file_path.suffix in [".md", ".txt"]:
            try:
                content = file_path.read_text(encoding="utf-8")
                # Category derived from immediate parent folder name
                category = file_path.parent.name
                doc_name = file_path.name

                metadata = {
                    "document_name": doc_name,
                    "category": category,
                    "source": str(file_path.relative_to(base_path)),
                    "file_path": str(file_path),
                }

                documents.append(Document(page_content=content, metadata=metadata))
            except Exception as e:
                print(f"Error loading file {file_path}: {e}")

    return documents
