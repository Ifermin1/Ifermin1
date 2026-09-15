import secrets

from fastapi import Depends, HTTPException, Request, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


def _expected(request_or_ws) -> str:
    return request_or_ws.app.state.container.settings.API_TOKEN


def _valid(token: str | None, expected: str) -> bool:
    return bool(token) and secrets.compare_digest(token, expected)


async def require_token(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    token = creds.credentials if creds else request.query_params.get("token")
    if not _valid(token, _expected(request)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido")


def ws_token_ok(ws: WebSocket) -> bool:
    token = ws.query_params.get("token")
    if not token:
        auth = ws.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip() or None
    return _valid(token, _expected(ws))
