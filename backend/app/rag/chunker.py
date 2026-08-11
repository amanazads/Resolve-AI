from typing import List
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

def split_documents(
    documents: List[Document],
    chunk_size: int = 600,
    chunk_overlap: int = 100
) -> List[Document]:
    """
    Splits documents into clean chunks and preserves metadata with unique chunk_ids.
    """
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", " ", ""]
    )
    
    chunks = text_splitter.split_documents(documents)
    
    # Assign unique chunk_id to each chunk metadata
    for idx, chunk in enumerate(chunks):
        doc_source = chunk.metadata.get("source", "unknown")
        chunk.metadata["chunk_id"] = f"{doc_source}_chunk_{idx}"
        
    return chunks
