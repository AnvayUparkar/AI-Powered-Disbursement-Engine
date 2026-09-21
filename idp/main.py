import asyncio
import os
from contextlib import asynccontextmanager

# Register PyTorch CUDA library directory for ONNX Runtime / RapidOCR on Windows
try:
    import torch
    torch_lib = os.path.join(os.path.dirname(torch.__file__), "lib")
    if os.path.exists(torch_lib):
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(torch_lib)
        os.environ["PATH"] = torch_lib + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.auth import require_tenant
from idp.api.routes import health, documents
from config.tenant import UnsafePathError
from idp.core.config import settings
from idp.core.exceptions import Node2BaseException
from idp.core.logging import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from idp.services.docling.pipeline import prewarm_docling_converters
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, prewarm_docling_converters)
    except Exception as exc:
        logger.warning(f"Failed to prewarm Docling converters: {exc}")
    yield


app = FastAPI(
    title=settings.APP_NAME,
    description="Node 2 — Intelligent Document Processing Engine for AI-Powered Disbursement Pipeline.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS Middleware - environment-based origins
allowed_origins = [
    origin.strip() for origin in settings.FRONTEND_ORIGIN.split(",") if origin.strip()
]
# Ensure standard local Vite dev ports are supported for local development
for local_origin in ["http://localhost:5173", "http://localhost:3000", "http://localhost:4173", "http://127.0.0.1:5173"]:
    if local_origin not in allowed_origins:
        allowed_origins.append(local_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers. Document routes require an authenticated tenant (browser session cookie, or the
# internal token + tenant headers sent by the Celery worker); health stays open. The auth DB schema is
# created lazily on the first cookie lookup, so a cluster IDP that only sees internal calls never opens it.
app.include_router(health.router)
app.include_router(documents.router, dependencies=[Depends(require_tenant)])


@app.exception_handler(UnsafePathError)
async def unsafe_path_handler(request: Request, exc: UnsafePathError):
    return JSONResponse(status_code=400, content={"error": "InvalidIdentifier", "message": str(exc)})


@app.exception_handler(Node2BaseException)
async def node2_exception_handler(request: Request, exc: Node2BaseException):
    logger.error(f"Node2BaseException caught in main app: {exc.message}")
    return JSONResponse(
        status_code=400,
        content={
            "error": exc.__class__.__name__,
            "message": exc.message,
            "details": exc.details
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("idp.main:app", host="0.0.0.0", port=settings.IDP_PORT, reload=True)
