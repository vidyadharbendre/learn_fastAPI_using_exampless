"""Run the Day 01 server without memorising uvicorn flags.

    python run.py

Equivalent to:

    uvicorn shelfspace.main:app --reload --port 8001
"""

import uvicorn

from shelfspace.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "shelfspace.main:app",
        host=settings.host,
        port=settings.port,
        # Reload watches the filesystem and restarts on save. Development only:
        # it runs a supervisor process and doubles memory for no benefit in prod.
        reload=not settings.is_production,
    )
