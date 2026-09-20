import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"


# Silence RapidOCR download_file existence checks from verbose logging
import logging
logging.getLogger("RapidOCR").setLevel(logging.WARNING)


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

    # Offline Execution & Model Weights
    OFFLINE_MODE: bool = True
    HF_HUB_OFFLINE: Optional[str] = None
    TRANSFORMERS_OFFLINE: Optional[str] = None
    MODEL_WEIGHTS_PATH: str = "models/"
    MODEL_WEIGHTS_S3_URI: Optional[str] = None

    # S3 Storage
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    S3_ENDPOINT_URL: Optional[str] = None
    S3_BUCKET: str = "disbursement-documents"
    RAW_DOCUMENT_PREFIX: str = "raw-documents/"
    PARSED_DOCUMENT_PREFIX: str = "parsed-documents/"

    # OCR Configuration
    OCR_ENGINE: str = "docling_ocr"
    OCR_MODEL: str = "PP-OCRv6_medium"
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
    VLM_ENABLED: bool = False
    VLM_PROVIDER: str = "mock"  # 'openai', 'gemini', 'mock'
    VLM_MODEL: str = "gpt-4o-mini"
    VLM_API_KEY: Optional[str] = None

    # Parallel Worker Concurrency
    MAX_PAGE_WORKERS: int = 2
    MAX_DOC_WORKERS: int = 2

    # Processing Limits
    MAX_DOCUMENT_SIZE_MB: int = 50
    TEMP_DIR: str = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "poc_data", "idp_temp")
    IDP_PORT: int = 8001


settings = Settings()

# Propagate environment settings loaded from .env to os.environ dynamically
if settings.OFFLINE_MODE or (settings.HF_HUB_OFFLINE and settings.HF_HUB_OFFLINE.lower() in ("true", "1", "yes")):
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
elif settings.HF_HUB_OFFLINE is not None:
    os.environ["HF_HUB_OFFLINE"] = str(settings.HF_HUB_OFFLINE)
    if settings.TRANSFORMERS_OFFLINE is not None:
        os.environ["TRANSFORMERS_OFFLINE"] = str(settings.TRANSFORMERS_OFFLINE)
