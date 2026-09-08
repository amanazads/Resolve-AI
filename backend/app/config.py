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

    # ==================== Outbound Email ====================
    # Which provider the application sends through: "mock" (default, local dev,
    # nothing leaves the process) or "gmail" (Gmail API over OAuth 2.0).
    EMAIL_PROVIDER: str = "mock"

    # ---- Google Cloud OAuth 2.0 client (Web application credentials) --------
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    # Must match an Authorized redirect URI on the OAuth client, exactly.
    GOOGLE_OAUTH_REDIRECT_URI: str = "http://localhost:8000/api/integrations/gmail/callback"
    # Space- or comma-separated. Leave blank to use DEFAULT_GMAIL_SCOPES.
    GMAIL_SCOPES: str = ""
    # Where the callback sends the browser once the flow finishes. Blank = JSON.
    GMAIL_OAUTH_SUCCESS_REDIRECT: str = ""

    # ---- Token storage -----------------------------------------------------
    # Fernet key (44-char urlsafe base64). Required before any OAuth token can
    # be persisted; without it the integration refuses to store credentials.
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    GMAIL_TOKEN_ENCRYPTION_KEY: str = ""

    # Identifier for the connected mailbox when the caller does not name one.
    GMAIL_DEFAULT_ACCOUNT_ID: str = "default"
    # Lifetime of a pending OAuth `state` value.
    GMAIL_OAUTH_STATE_TTL_SECONDS: int = 600
    GMAIL_HTTP_TIMEOUT_SECONDS: float = 20.0

    class Config:
        env_file = str(BASE_DIR / ".env")
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()
if settings.HF_TOKEN:
    os.environ["HF_TOKEN"] = settings.HF_TOKEN

