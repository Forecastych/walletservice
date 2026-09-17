"""HTTP primitives shared by the API layer.

Two small pieces live here so that neither the routers nor the exception
handlers have to touch symbols upstream has deprecated:

* :data:`HTTP_422_UNPROCESSABLE_CONTENT` — the RFC 9110 name for 422. Starlette
  deprecated ``status.HTTP_422_UNPROCESSABLE_ENTITY`` and emits a warning the
  moment the old attribute is read, which happened at import time through the
  route ``responses={...}`` tables. The value is spelled out once here, so it
  is correct on both old and new Starlette releases.
* :class:`ORJSONResponse` — an orjson-backed response for the handlers that
  build a response by hand. ``fastapi.responses.ORJSONResponse`` is deprecated
  because FastAPI now serialises route results to JSON bytes itself; exception
  handlers still have to return a concrete response, and this keeps their
  payloads byte-identical to what the service returned before.
"""

from __future__ import annotations

from typing import Any, Final

import orjson
from starlette.responses import JSONResponse

#: 422, under its current name. See RFC 9110 §15.5.21.
HTTP_422_UNPROCESSABLE_CONTENT: Final[int] = 422


class ORJSONResponse(JSONResponse):
    """``JSONResponse`` that renders through orjson instead of ``json``."""

    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return orjson.dumps(content)
