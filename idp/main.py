from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from idp.api.routes import health, documents
from idp.core.config import settings
from idp.core.exceptions import Node2BaseException
from idp.core.logging import logger

app = FastAPI(
    title=settings.APP_NAME,
    description="Node 2 — Intelligent Document Processing Engine for AI-Powered Disbursement Pipeline.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
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

# Include Routers
app.include_router(health.router)
app.include_router(documents.router)


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
