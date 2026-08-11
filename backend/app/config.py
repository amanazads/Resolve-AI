import os
from pathlib import Path
from pydantic_settings import BaseSettings

# Root directory of the project
BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Customer Support System"
    VERSION: str = "1.0.0"
    API_PREFIX: str = "/api"
    
    # Gemini API
    GEMINI_API_KEY: str = ""
    LLM_MODEL: str = "gemini-1.5-flash"
    
    # Databases
    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DB_NAME: str = "ai_support_db"
    CHROMA_PERSIST_DIRECTORY: str = str(BASE_DIR / "chroma_db")
    
    # RAG Settings
    KNOWLEDGE_BASE_DIR: str = str(BASE_DIR / "knowledge_base")
    TOP_K_RESULTS: int = 4
    
    # Logging
    LOG_LEVEL: str = "INFO"

    # Optional HuggingFace Token
    HF_TOKEN: str = ""

    class Config:
        env_file = str(BASE_DIR / ".env")
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()
if settings.HF_TOKEN:
    os.environ["HF_TOKEN"] = settings.HF_TOKEN

