"""简单的单密码认证中间件。"""
import hashlib
import os
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

APP_PASSWORD = os.environ.get("APP_PASSWORD", "admin")
COOKIE_NAME = "auth_token"
COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days

AUTH_WHITELIST = {
    "/login",
    "/api/auth/login",
    "/static",
}


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def check_auth(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME, "")
    return token == _hash(APP_PASSWORD)


def set_auth_cookie(response, remember: bool = True):
    response.set_cookie(
        COOKIE_NAME,
        _hash(APP_PASSWORD),
        max_age=COOKIE_MAX_AGE if remember else None,
        httponly=True,
        samesite="lax",
    )


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 白名单放行
        for prefix in AUTH_WHITELIST:
            if path.startswith(prefix):
                return await call_next(request)

        if not check_auth(request):
            return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)

        return await call_next(request)
