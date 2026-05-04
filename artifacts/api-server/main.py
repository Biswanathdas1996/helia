import logging
import os

import uvicorn

from app.server import app  # noqa: F401  (re-export for `uvicorn main:app`)

logger = logging.getLogger("api-server")


if __name__ == "__main__":
    raw_port = os.environ.get("PORT")
    if not raw_port:
        raise RuntimeError("PORT environment variable is required but was not provided.")
    try:
        port = int(raw_port)
        if port <= 0:
            raise ValueError
    except ValueError as e:
        raise RuntimeError(f"Invalid PORT value: {raw_port!r}") from e

    uvicorn.run("app.server:app", host="0.0.0.0", port=port, log_level="info")
