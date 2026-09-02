class Node2BaseException(Exception):
    """Base exception for Node 2 IDP Engine."""
    def __init__(self, message: str, details: str = ""):
        super().__init__(message)
        self.message = message
        self.details = details


class UnsupportedFileType(Node2BaseException):
    """Raised when an unsupported file mime type or extension is provided."""
    pass


class InvalidDocument(Node2BaseException):
    """Raised when a document is corrupted, unreadable, or empty."""
    pass


class DocumentTooLarge(Node2BaseException):
    """Raised when the document exceeds maximum configured size."""
    pass


class DoclingProcessingError(Node2BaseException):
    """Raised when Docling fails to parse document layout or tables."""
    pass


class OCRError(Node2BaseException):
    """Raised when RapidOCR / PP-OCRv6 processing encounters a failure."""
    pass


class VLMError(Node2BaseException):
    """Raised when VLM service call or structured JSON parsing fails."""
    pass


class S3Error(Node2BaseException):
    """Raised when S3 download or upload fails."""
    pass


class SerializationError(Node2BaseException):
    """Raised when building or serializing the canonical document representation fails."""
    pass
