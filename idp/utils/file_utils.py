import os
import shutil
import tempfile
import mimetypes
from typing import Tuple, Optional
from idp.core.exceptions import UnsupportedFileType, DocumentTooLarge
from idp.core.config import settings

SUPPORTED_MIME_TYPES = {
    "application/pdf": "pdf",
    "image/png": "image",
    "image/jpeg": "image",
    "image/jpg": "image",
    "image/tiff": "image",
    "application/xml": "xml",
    "text/xml": "xml"
}

SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tif": "image",
    ".tiff": "image",
    ".xml": "xml"
}


def detect_file_type(file_path: str) -> Tuple[str, str]:
    """
    Detect file category ('pdf', 'image', 'xml') and mime-type.

    Returns:
        Tuple[file_category, mime_type]
    """
    ext = os.path.splitext(file_path)[1].lower()
    mime_type, _ = mimetypes.guess_type(file_path)

    category = None
    # Primary check: match detected system MIME type
    if mime_type and mime_type in SUPPORTED_MIME_TYPES:
        category = SUPPORTED_MIME_TYPES[mime_type]
    # Disabled fallback check:
    # elif ext in SUPPORTED_EXTENSIONS:
    #     category = SUPPORTED_EXTENSIONS[ext]
    #     mime_type = mime_type or f"application/{ext.lstrip('.')}"

    if not category:
        raise UnsupportedFileType(f"Unsupported file type for {file_path}. Ext: '{ext}', Mime: '{mime_type}'")

    return category, mime_type


def validate_file_size(file_path: str, max_mb: int = settings.MAX_DOCUMENT_SIZE_MB) -> int:
    """Validate file exists and size does not exceed max_mb limit."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found at {file_path}")

    size_bytes = os.path.getsize(file_path)
    max_bytes = max_mb * 1024 * 1024
    if size_bytes > max_bytes:
        raise DocumentTooLarge(f"File size {size_bytes / (1024*1024):.2f}MB exceeds limit of {max_mb}MB")

    return size_bytes


def create_temp_dir(prefix: str = "node2_") -> str:
    """Create a managed temporary directory for document processing."""
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    return tempfile.mkdtemp(prefix=prefix, dir=settings.TEMP_DIR)


def cleanup_temp_dir(path: Optional[str]) -> None:
    """Safely remove temporary directory and its contents."""
    if path and os.path.exists(path):
        try:
            shutil.rmtree(path)
        except Exception:
            pass
