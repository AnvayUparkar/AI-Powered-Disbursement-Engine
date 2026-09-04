import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=(
            ".env",
            "../.env",
            "../../.env",
            os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"),
            os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
        ),
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

    # Multilingual OCR Router Settings
    ENGLISH_OCR_ENABLED: bool = True
    DEVANAGARI_OCR_ENABLED: bool = True
    JAPANESE_OCR_ENABLED: bool = True
    CHINESE_OCR_ENABLED: bool = False
    KOREAN_OCR_ENABLED: bool = False
    LATIN_MULTILINGUAL_OCR_ENABLED: bool = False
    DEFAULT_OCR_ROUTE: str = "english"

    # Script Routing & Profile Controls
    OCR_SCRIPT_ROUTING_ENABLED: bool = True
    OCR_DEFAULT_PROFILE: str = "english"
    OCR_PREVIEW_ROUTING_ENABLED: bool = True
    OCR_REGION_FALLBACK_ENABLED: bool = True

    # Document Type OCR Profile Hints
    DOCUMENT_OCR_PROFILES: dict = {
        "aadhaar": {"preferred_script": "devanagari", "allow_multilingual": True},
        "pan": {"preferred_script": "latin", "allow_multilingual": True},
        "bank_statement": {"preferred_script": "latin", "allow_multilingual": False},
        "loan_agreement": {"preferred_script": "latin", "allow_multilingual": False},
    }


    # VLM Configuration
    VLM_ENABLED: bool = True
    VLM_PROVIDER: str = "mock"  # 'openai', 'gemini', 'mock'
    VLM_MODEL: str = "gpt-4o-mini"
    VLM_API_KEY: Optional[str] = None

    # Parallel Worker Concurrency
    MAX_PAGE_WORKERS: int = 4
    MAX_DOC_WORKERS: int = 4

    # Processing Limits
    MAX_DOCUMENT_SIZE_MB: int = 50
    TEMP_DIR: str = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "poc_data", "idp_temp")
    IDP_PORT: int = 8001


settings = Settings()
