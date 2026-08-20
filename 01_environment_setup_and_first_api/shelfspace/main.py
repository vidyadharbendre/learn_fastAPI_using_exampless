"""The application: a factory, a lifespan, and three endpoints.

Read this file top to bottom — every block is a decision the README explains.
"""

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI

from . import __version__
from .config import Settings, get_settings
from .data import all_books
from .schemas import BookList, HealthResponse

logger = logging.getLogger("shelfspace")

# Set when the app starts serving, so /health can report real uptime.
_started_at: float = time.monotonic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown, in one place.

    Everything before `yield` runs once, before the first request is accepted;
    everything after runs on shutdown. This is where database pools, HTTP
    clients and caches are opened and closed in later days. Doing that work at
    import time instead is the classic way to make an app untestable.
    """
    global _started_at
    _started_at = time.monotonic()
    settings = get_settings()
    logger.info("starting %s v%s (%s)", settings.app_name, __version__, settings.environment)
    yield
    logger.info("shutting down %s", settings.app_name)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    A *factory* rather than a module-level `app = FastAPI()` because a test needs
    to build an app with different settings, and a module-level object can only
    ever be configured one way.
    """
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        summary="A bookstore API, built one day at a time.",
        description=(
            "Day 01 of a 21-day FastAPI course. Everything you see here — this "
            "page included — is generated from the type hints in the code."
        ),
        lifespan=lifespan,
        # Interactive docs are development conveniences, not public endpoints.
        # In production they advertise your whole attack surface for free.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    @app.get("/", tags=["meta"], summary="Service root")
    async def root() -> dict:
        """A discoverable root document.

        It costs one function and makes the API explorable with `curl` alone —
        no docs tab, no Postman collection, no asking a colleague.
        """
        return {
            "service": settings.app_name,
            "version": __version__,
            "docs": "/docs" if not settings.is_production else None,
            "endpoints": {
                "health": "/health",
                "books": "/books",
            },
        }

    @app.get(
        "/health",
        tags=["meta"],
        summary="Liveness and version probe",
        response_model=HealthResponse,
    )
    async def health() -> HealthResponse:
        """The first endpoint any real service gets.

        Deployment tooling polls this to decide whether to route traffic here,
        and `version` is the field you will be grateful for at 3 a.m. when you
        need to know which build is actually running.
        """
        return HealthResponse(
            status="ok",
            service=settings.app_name,
            version=__version__,
            environment=settings.environment,
            uptime_seconds=round(time.monotonic() - _started_at, 3),
            checked_at=datetime.now(timezone.utc),
        )

    @app.get(
        "/books",
        tags=["catalogue"],
        summary="List the catalogue",
        response_model=BookList,
    )
    async def list_books() -> BookList:
        """Today it reads a list; by Day 12 it paginates, filters and sorts.

        Note the envelope: `{"count": n, "items": [...]}`, never a bare array.
        """
        books = all_books()
        return BookList(count=len(books), items=books)

    return app


# Uvicorn's default target: `uvicorn shelfspace.main:app`.
app = create_app()
