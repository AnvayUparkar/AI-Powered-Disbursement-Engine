import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False
    )

    # General
    APP_ENV: str = "development"
    APP_NAME: str = "Node 2 — Intelligent Document Processing Engine"
    LOG_LEVEL: str = "INFO"
    DEBUG: bool = False
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    # S3 Storage
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    S3_ENDPOINT_URL: Optional[str] = None
    S3_BUCKET: str = "disbursement-documents"
    RAW_DOCUMENT_PREFIX: str = "raw-documents/"
    PARSED_DOCUMENT_PREFIX: str = "parsed-documents/"

    # OCR Configuration
    OCR_ENGINE: str = "rapidocr"
    OCR_MODEL: str = "PP-OCRv6"
    OCR_CONFIDENCE_THRESHOLD: float = 0.70

    # VLM Configuration
    VLM_ENABLED: bool = True
    VLM_PROVIDER: str = "mock"  # 'openai', 'gemini', 'mock'
    VLM_MODEL: str = "gpt-4o-mini"
    VLM_API_KEY: Optional[str] = None

    # Processing Limits
    MAX_DOCUMENT_SIZE_MB: int = 50
    TEMP_DIR: str = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "poc_data", "idp_temp")
    IDP_PORT: int = 8001


settings = Settings()
