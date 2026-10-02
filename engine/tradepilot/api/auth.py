import secrets
import time

from fastapi import Depends, HTTPException, Request, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger

_bearer = HTTPBearer(auto_error=False)

# Freno a la fuerza bruta cuando la consola está publicada en Internet: tras MAX_FAILS tokens inválidos desde la misma IP
# en WINDOW_S segundos, esa IP queda bloqueada LOCK_S segundos (también con el token correcto) y se audita una vez.
MAX_FAILS, WINDOW_S, LOCK_S = 10, 600, 300
_fails: dict[str, list[float]] = {}
_locked: dict[str, float] = {}


def reset_throttle() -> None:
    _fails.clear(); _locked.clear()


def client_ip(req) -> str:
    """IP real del cliente: detrás de Cloudflare Tunnel llega en CF-Connecting-IP; detrás de otro proxy en X-Forwarded-For."""
    h = req.headers
    ip = h.get("cf-connecting-ip") or (h.get("x-forwarded-for") or "").split(",")[0].strip()
    if not ip and req.client:
        ip = req.client.host
    return ip or "desconocida"


def _expected(request_or_ws) -> str:
    return request_or_ws.app.state.container.settings.API_TOKEN


def _valid(token: str | None, expected: str) -> bool:
    return bool(token) and secrets.compare_digest(token, expected)


def _note(req, ok: bool) -> str | None:
    """Devuelve None si se puede seguir; si no, el motivo ("locked" o "invalid")."""
    ip = client_ip(req)
    now = time.monotonic()
    until = _locked.get(ip)
    if until is not None:
        if now < until:
            return "locked"
        del _locked[ip]
    if ok:
        _fails.pop(ip, None)
        return None
    fails = [t for t in _fails.get(ip, []) if now - t < WINDOW_S] + [now]
    _fails[ip] = fails
    if len(fails) >= MAX_FAILS:
        _locked[ip] = now + LOCK_S
        _fails.pop(ip, None)
        logger.warning(f"IP {ip} bloqueada {LOCK_S} s tras {MAX_FAILS} tokens inválidos")
        try:
            req.app.state.container.audit.log("AUTH_LOCKED", f"IP {ip} bloqueada {LOCK_S // 60} min tras {MAX_FAILS} intentos con token inválido",
                                              details={"ip": ip})
        except Exception:
            pass
        return "locked"
    return "invalid"


async def require_token(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    token = creds.credentials if creds else request.query_params.get("token")
    why = _note(request, _valid(token, _expected(request)))
    if why == "locked":
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Demasiados intentos: espera unos minutos")
    if why == "invalid":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido")


def ws_token_ok(ws: WebSocket) -> bool:
    token = ws.query_params.get("token")
    if not token:
        auth = ws.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip() or None
    return _note(ws, _valid(token, _expected(ws))) is None
