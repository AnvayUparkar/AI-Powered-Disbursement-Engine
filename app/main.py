import json
import logging
import os
import sys
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routers import cases, dashboard_reports, documents, loans, reviews
from idp.api.routes import documents as idp_documents
from idp.core.exceptions import Node2BaseException


# Structured JSON Formatter
class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


# Setup root and pipeline loggers
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JSONFormatter())
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers = [handler]

app = FastAPI(
    title="Disbursement Scorecard Pipeline API",
    description="Backend API for automated loan disbursement verification POC",
    version="1.0.0",
)

# Configurable CORS Origins with strict defaults
raw_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000",
)
allowed_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start_time) * 1000
    root_logger.info(
        json.dumps({
            "event": "http_request",
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round(duration_ms, 2),
        })
    )
    return response


@app.exception_handler(Node2BaseException)
async def node2_exception_handler(request: Request, exc: Node2BaseException):
    root_logger.error(f"Node2BaseException caught in main app: {exc.message}")
    return JSONResponse(
        status_code=400,
        content={
            "error": exc.__class__.__name__,
            "message": exc.message,
            "details": exc.details,
        },
    )


app.include_router(loans.router)
app.include_router(cases.router)
app.include_router(reviews.router)
app.include_router(documents.router)
app.include_router(dashboard_reports.router)
app.include_router(idp_documents.router)


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok", "service": "disbursement-scorecard-poc"}

