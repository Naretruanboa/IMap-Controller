import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from api.endpoints import router
from annotation.api import router as annotation_router
from annotation.api import init_service as init_annotation
from annotation.database import AnnotationDatabase
from annotation.service import AnnotationService
from services.controller import Controller

load_dotenv()
ROOT = Path(__file__).parent
logger = logging.getLogger(__name__)
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def configured_log_level() -> str:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    return level if level in LOG_LEVELS else "INFO"


def create_app(provider: str | None = None, database: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        mode = provider or os.getenv("GPS_PROVIDER", "iphone")
        hz = float(os.getenv("MOVEMENT_HZ", "10"))
        speed = float(os.getenv("DEFAULT_SPEED_KMH", "5"))
        if mode not in ("mock", "iphone", "android") or not 5 <= hz <= 20 or not 0.1 <= speed <= 200:
            raise ValueError("Invalid provider, movement frequency or default speed")
        c = Controller(mode, database or os.getenv("DATABASE_PATH", str(ROOT / "data/app.db")), hz, speed)
        app.state.controller = c

        # Annotation editor
        ann_db = AnnotationDatabase(str(ROOT / "data/annotation.db"))
        ann_svc = AnnotationService(str(ROOT / "dataset"), ann_db)
        init_annotation(ann_svc)
        ann_svc.sync_images()
        app.state.annotation_service = ann_svc

        try:
            await c.start()
            yield
        finally:
            await c.close()
            ann_svc.close()

    app = FastAPI(title="Pokemon GO Controller", lifespan=lifespan)
    hosts = ["127.0.0.1", "localhost", "[::1]", "testserver"]
    hosts.extend(filter(None, os.getenv("ALLOWED_HOSTS", "").split(",")))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and origin
            and origin != str(request.base_url).rstrip("/")
        ):
            return JSONResponse({"detail": "Cross-origin control is disabled"}, status_code=403)
        length = request.headers.get("content-length", "0")
        if not length.isdigit() or int(length) > 50_000_000:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if request.url.path.startswith("/annotation") or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        logger.debug("HTTP %s %s -> %s", request.method, request.url.path, response.status_code)
        return response

    @app.exception_handler(ConnectionError)
    async def connection_error(request: Request, exc: ConnectionError):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "static/index.html")

    @app.get("/annotation")
    async def annotation_page():
        return FileResponse(ROOT / "static/annotation.html")

    app.include_router(router)
    app.include_router(annotation_router)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    level = configured_log_level()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        ws_max_size=4096,
        log_level=level.lower(),
    )
