import os
import logging
from typing import List, Tuple
try:
    from langchain_chroma import Chroma
except ImportError:
    from langchain_community.vectorstores import Chroma
try:
    from app.config import settings
    from app.rag.embeddings import get_embedding_function
except ImportError:
    from ..config import settings
    from .embeddings import get_embedding_function

logger = logging.getLogger(__name__)

COLLECTION_NAME = "support_knowledge_base"

class VectorStoreManager:
    def __init__(self):
        self.embedding_fn = get_embedding_function()
        self.persist_directory = settings.CHROMA_PERSIST_DIRECTORY
        self._vectorstore = None

    def get_vectorstore(self) -> Chroma:
        if self._vectorstore is None:
            self._vectorstore = Chroma(
                collection_name=COLLECTION_NAME,
                embedding_function=self.embedding_fn,
                persist_directory=self.persist_directory
            )
        return self._vectorstore

    def build_from_documents(self, documents: List[Document]) -> Chroma:
        """
        Recreates collection from document chunks.
        """
        logger.info(f"Indexing {len(documents)} chunks into ChromaDB at {self.persist_directory}")
        
        try:
            temp_vs = Chroma(collection_name=COLLECTION_NAME, embedding_function=self.embedding_fn, persist_directory=self.persist_directory)
            temp_vs.delete_collection()
        except Exception as e:
            logger.info(f"Clearing old collection prior to rebuild: {e}")

        # Initialize fresh collection
        self._vectorstore = Chroma.from_documents(
            documents=documents,
            embedding=self.embedding_fn,
            collection_name=COLLECTION_NAME,
            persist_directory=self.persist_directory
        )
        logger.info("ChromaDB indexing completed successfully.")
        return self._vectorstore

    def similarity_search_with_score(
        self, query: str, k: int = settings.TOP_K_RESULTS
    ) -> List[Tuple[Document, float]]:
        vs = self.get_vectorstore()
        try:
            return vs.similarity_search_with_score(query, k=k)
        except Exception as e:
            logger.error(f"Error during vector search: {e}")
            return []

vector_store_manager = VectorStoreManager()
