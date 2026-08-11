import logging
from typing import Dict, Any, List
try:
    from app.config import settings
    from app.rag.vectorstore import vector_store_manager
except ImportError:
    from ..config import settings
    from .vectorstore import vector_store_manager

logger = logging.getLogger(__name__)

def retrieve_context(query: str, top_k: int = settings.TOP_K_RESULTS) -> Dict[str, Any]:
    """
    Performs semantic retrieval against ChromaDB for a user query.
    Returns:
      - retrieved_chunks: List of document dicts with content, score, and metadata
      - context_text: Concatenated context string for LLM prompt
      - sources: List of unique source document names
    """
    logger.info(f"Retrieving top {top_k} documents for query: '{query}'")
    results = vector_store_manager.similarity_search_with_score(query, k=top_k)
    
    retrieved_chunks = []
    sources = set()
    context_passages = []
    
    for doc, score in results:
        source_name = doc.metadata.get("source", doc.metadata.get("document_name", "unknown"))
        sources.add(source_name)
        
        chunk_info = {
            "content": doc.page_content,
            "score": float(score),
            "source": source_name,
            "metadata": doc.metadata
        }
        retrieved_chunks.append(chunk_info)
        context_passages.append(f"--- Document: {source_name} ---\n{doc.page_content}")
        
    context_text = "\n\n".join(context_passages)
    
    return {
        "retrieved_chunks": retrieved_chunks,
        "context_text": context_text,
        "sources": list(sources)
    }
