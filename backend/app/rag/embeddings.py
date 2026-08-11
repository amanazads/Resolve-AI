import os
import logging
from typing import List

try:
    from app.config import settings
except ImportError:
    from ..config import settings

logger = logging.getLogger(__name__)

class SafeEmbeddings:
    """
    Wrapper around primary and fallback embedding models.
    Tries Google Gemini Embeddings if available; falls back smoothly to HuggingFace.
    """
    def __init__(self, primary_embeddings, fallback_embeddings):
        self.primary = primary_embeddings
        self.fallback = fallback_embeddings

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if self.primary:
            try:
                return self.primary.embed_documents(texts)
            except Exception as e:
                logger.warning(f"Primary embedding failed ({e}). Falling back to local embeddings.")
        return self.fallback.embed_documents(texts)

    def embed_query(self, text: str) -> List[float]:
        if self.primary:
            try:
                return self.primary.embed_query(text)
            except Exception as e:
                logger.warning(f"Primary query embedding failed ({e}). Falling back to local embeddings.")
        return self.fallback.embed_query(text)

def get_embedding_function():
    """
    Returns a SafeEmbeddings instance that uses local HuggingFace embeddings as a fail-safe.
    """
    fallback_fn = None
    try:
        # pyrefly: ignore [missing-import]
        from langchain_huggingface import HuggingFaceEmbeddings
        fallback_fn = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    except Exception:
        try:
            # pyrefly: ignore [missing-import]
            from langchain_community.embeddings import HuggingFaceEmbeddings
            fallback_fn = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        except Exception as e:
            logger.error(f"Failed to load HuggingFaceEmbeddings: {e}")
            class DummyEmbeddings:
                def embed_documents(self, texts: List[str]) -> List[List[float]]:
                    return [[0.0] * 384 for _ in texts]
                def embed_query(self, text: str) -> List[float]:
                    return [0.0] * 384
            fallback_fn = DummyEmbeddings()

    api_key = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY")
    primary_fn = None
    
    if api_key and api_key.strip():
        try:
            # pyrefly: ignore [missing-import]
            from langchain_google_genai import GoogleGenerativeAIEmbeddings
            # Use standard embedding model name
            primary_fn = GoogleGenerativeAIEmbeddings(
                model="models/gemini-embedding-001",
                google_api_key=api_key,
                max_retries=1
            )
        except Exception as e:
            logger.warning(f"Could not initialize GoogleGenerativeAIEmbeddings: {e}")

    return SafeEmbeddings(primary_embeddings=primary_fn, fallback_embeddings=fallback_fn)
