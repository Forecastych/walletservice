"""Run the service with uvicorn: ``python -m app``."""

from __future__ import annotations

import uvicorn

from app.core.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # noqa: S104 - bound inside a container network
        port=8000,
        log_config=None,  # JSON logging is configured by the application
        access_log=False,  # the request middleware logs structured access lines
    )


if __name__ == "__main__":
    main()
