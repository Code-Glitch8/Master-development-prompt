"""Project Pilot backend entry point.

Run: `python -m app.main` (dev) or via the packaged executable.
Binds to 127.0.0.1 only. The dashboard is served from the same origin.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import accounts, files, projects, system, tasks
from .config import get_settings
from .database import create_all, init_engine, session_scope
from .models import ServiceState
from .providers import seed_providers
from .services.events import bus
from .services.settings_service import get_setting
from .utils.logging_setup import get_logger, setup_logging
from .workers.engine import engine

log = get_logger("main")

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    s = get_settings()
    s.ensure_dirs()
    init_engine()
    create_all()
    with session_scope() as db:
        seed_providers(db)
        auto = get_setting(db, "worker.auto_start", True)
        last = get_setting(db, "engine.state", ServiceState.RUNNING.value)
    bus.bind_loop(asyncio.get_running_loop())
    # Tasks and state survive restarts; resume the engine unless the user had stopped it.
    if auto and last != ServiceState.STOPPED.value and not getattr(app.state, "no_engine", False):
        await engine.start(recover=True)
        if last == ServiceState.PAUSED.value:
            engine.pause()
    log.info("Project Pilot %s ready on http://%s:%s", __version__, s.host, s.port)
    try:
        yield
    finally:
        if engine.loop_alive:
            await engine.stop(timeout=10, notify_user=False)


def create_app(no_engine: bool = False) -> FastAPI:
    app = FastAPI(title="Project Pilot", version=__version__, lifespan=lifespan)
    app.state.no_engine = no_engine

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Defend against DNS-rebinding and cross-site requests to the local API.
        host = (request.headers.get("host") or "").rsplit(":", 1)[0] if not (request.headers.get("host") or "").startswith("[") \
            else (request.headers.get("host") or "").split("]")[0] + "]"
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
            oh = origin.split("://", 1)[-1].rsplit(":", 1)[0]
            if oh not in ALLOWED_HOSTS:
                return JSONResponse({"detail": "cross-origin request blocked"}, status_code=403)
        return await call_next(request)

    for r in (projects.router, tasks.router, accounts.router, files.router, system.router):
        app.include_router(r)

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        origin = websocket.headers.get("origin")
        if origin and origin.split("://", 1)[-1].rsplit(":", 1)[0] not in ALLOWED_HOSTS:
            await websocket.close(code=4403)
            return
        await websocket.accept()
        q = bus.subscribe()
        try:
            await websocket.send_text(json.dumps({"type": "hello", "engine": engine.state}))

            async def pump():
                while True:
                    ev = await q.get()
                    await websocket.send_text(json.dumps(ev, default=str))

            pump_task = asyncio.create_task(pump())
            try:
                while True:
                    msg = await websocket.receive_text()  # keepalive pings from the client
                    if msg == "ping":
                        await websocket.send_text('{"type":"pong"}')
            finally:
                pump_task.cancel()
        except WebSocketDisconnect:
            pass
        except Exception:  # pragma: no cover
            pass
        finally:
            bus.unsubscribe(q)

    dist = get_settings().frontend_dist
    if dist.exists():
        assets = dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse({"detail": "not found"}, status_code=404)
            candidate = (dist / full_path).resolve()
            if full_path and dist.resolve() in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        async def no_frontend():
            return JSONResponse({"detail": "Frontend not built. Run `npm run build` in /frontend, or use the "
                                           "Vite dev server. The API is available at /api and docs at /docs."})
    return app


app = create_app()


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Project Pilot local service")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true", help="run headless (background service only)")
    args = parser.parse_args()
    s = get_settings()
    if args.port:
        s.port = args.port
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{s.host}:{s.port}")).start()
    uvicorn.run(app, host=s.host, port=s.port, log_level="info")


if __name__ == "__main__":
    main()
