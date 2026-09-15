from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from tradepilot.api.auth import ws_token_ok
from tradepilot.api.routes import router
from tradepilot.api.ws import WsHub
from tradepilot.container import Container, build_container


def create_app(container: Container | None = None) -> FastAPI:
    container = container or build_container()
    hub = WsHub()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # todo evento del bus se reenvía a los WebSockets conectados
        async def relay(envelope: dict):
            await hub.broadcast(envelope["topic"], envelope["data"])

        container.bus.subscribe("*", relay)
        await container.start()
        try:
            yield
        finally:
            await container.stop()

    app = FastAPI(title="TradePilot X API", version="0.2.0", lifespan=lifespan)
    app.state.container = container
    app.state.ws_hub = hub

    origins = [o.strip() for o in container.settings.CORS_ORIGINS.split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins or ["*"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(router)

    @app.websocket("/api/ws")
    async def ws_endpoint(ws: WebSocket):
        if not ws_token_ok(ws):
            await ws.close(code=4401)
            return
        await hub.connect(ws)
        try:
            # estado inicial para que la UI no espere al siguiente ciclo
            await ws.send_json({"topic": "accounts.snapshot",
                                "data": [a.model_dump(mode="json") for a in container.accounts.all()]})
            await ws.send_json({"topic": "broker.health", "data": container.accounts.health().model_dump(mode="json")})
            await ws.send_json({"topic": "risk.state", "data": container.risk.state().model_dump(mode="json")})
            while True:
                await ws.receive_text()  # pings del cliente; ignoramos contenido
        except WebSocketDisconnect:
            pass
        finally:
            await hub.disconnect(ws)

    # Consola web compilada (web/dist) servida desde el mismo proceso
    dist = Path(container.settings.WEB_DIST)
    if not dist.is_absolute():
        dist = (Path(__file__).resolve().parents[2] / dist).resolve()
    if (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            candidate = dist / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

        logger.info(f"Sirviendo consola web desde {dist}")
    else:
        logger.warning(f"No hay build web en {dist}; solo API. Ejecuta `npm run build` en web/.")

    return app
