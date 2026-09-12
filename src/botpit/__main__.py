"""``python -m botpit`` runs the dev server with settings from the environment."""

from __future__ import annotations

import uvicorn

from botpit.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "botpit.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_config=None,
    )


if __name__ == "__main__":
    main()
