"""Auth endpoints — signup, login, logout, and current-session lookup."""
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app import auth
from config.settings import SESSION_COOKIE_SECURE, SESSION_TTL_HOURS

router = APIRouter(prefix="/api/auth", tags=["Auth"])


class Credentials(BaseModel):
    username: str
    password: str


def _client_id(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=auth.SESSION_COOKIE,
        value=token,
        max_age=int(SESSION_TTL_HOURS * 3600),
        httponly=True,
        samesite="lax",
        secure=SESSION_COOKIE_SECURE,
        path="/",
    )


def _start_session(response: Response, user_id: int, tenant_id: str) -> None:
    _set_session_cookie(response, auth.create_session(user_id, tenant_id))


@router.post("/signup", summary="Create an account (each signup gets its own isolated workspace)")
def signup(payload: Credentials, request: Request, response: Response):
    auth.init_db()
    try:
        auth.create_tenant_user(payload.username, payload.password)
        user_id, tenant_id = auth.authenticate(payload.username, payload.password, _client_id(request))
    except auth.AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    _start_session(response, user_id, tenant_id)
    return {"username": payload.username}


@router.post("/login", summary="Log in with username and password")
def login(payload: Credentials, request: Request, response: Response):
    auth.init_db()
    try:
        user_id, tenant_id = auth.authenticate(payload.username, payload.password, _client_id(request))
    except auth.AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    _start_session(response, user_id, tenant_id)
    return {"username": payload.username}


@router.post("/logout", summary="Log out and invalidate the session")
def logout(request: Request, response: Response):
    auth.delete_session(request.cookies.get(auth.SESSION_COOKIE))
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"status": "logged_out"}


@router.get("/me", summary="Current user, or 401 if not logged in")
async def me(request: Request):
    session = await run_in_threadpool(auth.lookup_session, request.cookies.get(auth.SESSION_COOKIE))
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"username": session["username"]}
